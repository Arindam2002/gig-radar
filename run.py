#!/usr/bin/env python3
"""Job Scout pipeline: fetch -> score -> store.

  python run.py                     full run over all enabled sources
  python run.py --source linkedin   one source
  python run.py --dry-run           fetch + score, no DB writes
  python run.py --contract-check    live endpoint health, no DB writes
  python run.py --prospects         discover speculative-outreach companies
"""
import argparse
import importlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from jobscout import db  # noqa: E402
from jobscout.outreach import (cold_mail_suitability, discover_prospects,  # noqa: E402
                               extract_emails)
from jobscout.normalize import parse_experience  # noqa: E402
from jobscout.score import score_job  # noqa: E402
from jobscout.sources import linkedin_guest  # noqa: E402
from jobscout.sources.base import mentions_relocation  # noqa: E402

SOURCE_MODULES = ["linkedin", "naukri", "himalayas", "remotive", "remoteok",
                  "arbeitnow", "adzuna", "jsearch"]
_MODULE_NAME = {"linkedin": "linkedin_guest"}


def load_configs():
    from jobscout.settings import load_configs as _load
    return _load()


def enabled_sources(cfg):
    out = []
    keys = cfg.get("keys", {})
    for name in SOURCE_MODULES:
        on = cfg.get("sources", {}).get(name, False)
        if name == "adzuna":
            on = on or bool(keys.get("adzuna_app_id") and keys.get("adzuna_app_key"))
        if name == "jsearch":
            on = on or bool(keys.get("jsearch_rapidapi_key"))
        if on:
            out.append(name)
    return out


def ingest_job(conn, job, profile, cfg, counts):
    """Score + contact-extract + upsert one Job."""
    # experience requirement: Naukri meta ("0-2") or the JD text itself
    exp_src = job.extra.get("experience", "")
    exp_min, exp_max = parse_experience(f"{exp_src} yrs" if exp_src else "")
    if exp_min is None:
        exp_min, exp_max = parse_experience(f"{job.title} {job.description}")
    job.extra["exp_min"], job.extra["exp_max"] = exp_min, exp_max
    if mentions_relocation(job.description):
        job.extra["relocation"] = True

    emails = extract_emails(job.description)
    if emails and not job.extra.get("contact_email"):
        job.extra["contact_email"] = emails[0]
        job.extra["contact_email_source"] = "found in posting"

    score, matched = score_job(job.title, job.description, job.skills,
                               job.company, profile, cfg)

    def rescore(title, description, skills):
        return score_job(title, description, skills, job.company, profile, cfg)

    result = db.upsert(conn, job, score, matched,
                       freshness_hours=cfg["search"].get("freshness_hours", 48),
                       rescore=rescore)
    counts[result] = counts.get(result, 0) + 1

    # every company gets a row (for tier classification); cold-mail-suitable
    # ones with a decent match are promoted to prospects
    promote = (score >= 40 and
               cold_mail_suitability(job.company, cfg["companies"].get("boost")) == "good")
    db.upsert_company(conn, job.company,
                      status="prospect" if promote else "tracked",
                      li_url=job.extra.get("company_li_url", ""),
                      suitability="good" if promote else "")


def enrich_linkedin(conn, profile, cfg):
    """Stage 2: fetch full JDs for the best thin LinkedIn rows, on a budget."""
    budget = cfg.get("linkedin", {}).get("detail_budget_per_run", 15)
    rows = conn.execute(
        """SELECT id, url, title, company FROM jobs
           WHERE source='linkedin' AND needs_detail=1 AND status='new'
           ORDER BY score DESC, posted_at_epoch DESC LIMIT ?""", (budget,)).fetchall()
    if not rows:
        return
    print(f"[linkedin stage-2] enriching {len(rows)} listings…")
    enriched = 0
    for row in rows:
        detail = linkedin_guest.enrich(row["url"])
        if detail.get("error"):
            print(f"  [linkedin stage-2] {row['url'][:60]}: {detail['error']} — stopping stage 2")
            break
        desc = detail.get("description", "")
        if not desc:
            conn.execute("UPDATE jobs SET needs_detail=0 WHERE id=?", (row["id"],))
            conn.commit()
            continue
        score, matched = score_job(row["title"], desc, [], row["company"], profile, cfg)
        emails = extract_emails(desc)
        exp_min, exp_max = parse_experience(desc)
        conn.execute(
            "UPDATE jobs SET exp_min=COALESCE(exp_min, ?), exp_max=COALESCE(exp_max, ?), "
            "relocation=CASE WHEN ?=1 THEN 1 ELSE relocation END WHERE id=?",
            (exp_min, exp_max, int(mentions_relocation(desc)), row["id"]))
        conn.execute(
            """UPDATE jobs SET description=?, needs_detail=0, score=?,
               matched_skills=?, company_li_url=COALESCE(NULLIF(company_li_url,''), ?),
               contact_email=CASE WHEN contact_email='' THEN ? ELSE contact_email END,
               contact_email_source=CASE WHEN contact_email='' AND ?!=''
                   THEN 'found in posting' ELSE contact_email_source END
               WHERE id=?""",
            (desc, score, json.dumps(matched),
             detail.get("company_li_url", ""), emails[0] if emails else "",
             emails[0] if emails else "", row["id"]))
        conn.commit()
        enriched += 1
    print(f"[linkedin stage-2] enriched {enriched}")


def llm_enrich(conn, cfg):
    """Gemini structured extraction over new JDs regex couldn't fully parse.

    Fills exp/salary/seniority; archives rows the JD explicitly restricts to
    non-India countries. Budget-capped; skipped without a key.
    """
    key = cfg.get("keys", {}).get("gemini_api_key")
    if not key:
        return
    import time

    from jobscout.llm import RateLimited, extract_job_facts
    llm_cfg = cfg.get("llm", {})
    budget = llm_cfg.get("extract_budget_per_run", 25)
    model = llm_cfg.get("extract_model") or llm_cfg.get("model", "gemini-3.1-flash-lite")
    pace = llm_cfg.get("seconds_between_calls", 6)  # ~10 RPM free-tier cap
    rows = conn.execute(
        """SELECT id, title, description FROM jobs
           WHERE status='new' AND llm_extracted=0 AND length(description) >= 300
           AND (exp_min IS NULL OR salary_min IS NULL OR seniority='')
           ORDER BY score DESC LIMIT ?""", (budget,)).fetchall()
    if not rows:
        return
    print(f"[llm] extracting facts from {len(rows)} JDs (gemini, {pace}s pace)…")
    done = archived = 0
    retried = False
    for row in rows:
        time.sleep(pace)
        try:
            f = extract_job_facts(row["title"], row["description"], key, model)
        except RateLimited as e:
            print(f"  [llm] {e} — exhausted quota: {e.quota_info}")
            if retried or e.retry_seconds > 120:
                print("  [llm] stopping pass; remaining JDs picked up next run")
                break
            retried = True
            time.sleep(min(e.retry_seconds + 1, 120))
            try:
                f = extract_job_facts(row["title"], row["description"], key, model)
            except Exception:
                print("  [llm] still limited — stopping pass")
                break
        except Exception as e:
            print(f"  [llm] {row['title'][:40]!r} failed: {str(e)[:100]} — stopping pass")
            break
        sal_min = sal_max = None
        cur = ""
        if f["salary_min_lpa"]:
            sal_min, sal_max, cur = f["salary_min_lpa"], f["salary_max_lpa"] or f["salary_min_lpa"], "INR"
        elif f["salary_min_usd"]:
            sal_min, sal_max, cur = f["salary_min_usd"], f["salary_max_usd"] or f["salary_min_usd"], "USD"
        conn.execute(
            """UPDATE jobs SET llm_extracted=1,
               exp_min=COALESCE(exp_min, ?), exp_max=COALESCE(exp_max, ?),
               seniority=CASE WHEN seniority='' THEN ? ELSE seniority END,
               salary_min=COALESCE(salary_min, ?), salary_max=COALESCE(salary_max, ?),
               salary_currency=CASE WHEN salary_currency='' AND ? != '' THEN ?
                                    ELSE salary_currency END
               WHERE id=?""",
            (f["exp_min"], f["exp_max"], f["seniority"], sal_min, sal_max,
             cur, cur, row["id"]))
        if f.get("visa_or_relocation"):
            conn.execute("UPDATE jobs SET relocation=1 WHERE id=?", (row["id"],))
        if not f["india_eligible"]:
            # foreign-restricted role -> Abroad category (relocation upside),
            # not deleted: the user is open to relocating
            conn.execute("UPDATE jobs SET abroad=1 WHERE id=?", (row["id"],))
            archived += 1
        conn.commit()
        done += 1
    print(f"[llm] extracted {done}, tagged {archived} as abroad")


def run_pipeline(args):
    cfg, profile = load_configs()
    conn = None if args.dry_run else db.init_db()
    sources = [args.source] if args.source else enabled_sources(cfg)
    summary = {}

    for name in sources:
        mod = importlib.import_module(f"jobscout.sources.{_MODULE_NAME.get(name, name)}")
        print(f"[{name}] fetching…")
        counts: dict = {}
        try:
            jobs = mod.fetch(cfg)
        except Exception as e:
            print(f"[{name}] FAILED: {e}")
            summary[name] = {"error": str(e)}
            continue
        if args.dry_run:
            scored = [(score_job(j.title, j.description, j.skills, j.company,
                                 profile, cfg)[0], j) for j in jobs]
            scored.sort(key=lambda x: -x[0])
            summary[name] = {"fetched": len(jobs)}
            for s, j in scored[:5]:
                print(f"    {s:5.1f}  {j.title[:45]} | {j.company[:25]}")
            continue
        for job in jobs:
            try:
                ingest_job(conn, job, profile, cfg, counts)
            except Exception as e:
                print(f"  [{name}] ingest failed for {job.title[:40]!r}: {e}")
        counts["fetched"] = len(jobs)
        summary[name] = counts

    if conn is not None:
        if "linkedin" in sources:
            enrich_linkedin(conn, profile, cfg)
        llm_enrich(conn, cfg)
        from jobscout.tiers import classify_companies
        tc = classify_companies(conn, cfg)
        print(f"[tiers] classified: {tc['heuristic']} heuristic, {tc['gemini']} gemini, "
              f"{tc['pending']} pending")
        archived = db.archive_backlog(conn)
        if archived:
            print(f"[archive] {archived} junk/stale rows archived")

    print("\n── run summary ──")
    for name, c in summary.items():
        if "error" in c:
            print(f"  {name:<10} ERROR: {c['error'][:80]}")
        else:
            print(f"  {name:<10} fetched={c.get('fetched', 0)} new={c.get('new', 0)} "
                  f"merged={c.get('merged', 0)}")

    if conn is not None:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        top = conn.execute(
            """SELECT score, title, company, salary_text FROM jobs
               WHERE status='new' ORDER BY score DESC LIMIT 10""").fetchall()
        print(f"\n  DB total: {total} jobs. Top new matches:")
        for r in top:
            sal = f" | {r['salary_text']}" if r["salary_text"] else ""
            print(f"    {r['score']:5.1f}  {r['title'][:45]} | {r['company'][:25]}{sal}")


def contract_check():
    """Live endpoint health, no DB writes. Loud pass/fail per source."""
    cfg, _ = load_configs()
    from jobscout.sources.base import get
    checks = {
        "linkedin": lambda: "base-card" in get(
            linkedin_guest.SEARCH,
            params={"keywords": "backend engineer", "location": "India", "start": 0},
            delay=1).text,
        "himalayas": lambda: "jobs" in get("https://himalayas.app/jobs/api?limit=1", delay=0.5).json(),
        "remotive": lambda: "jobs" in get("https://remotive.com/api/remote-jobs", delay=0.5).json(),
        "remoteok": lambda: isinstance(get("https://remoteok.com/api", delay=0.5).json(), list),
        "arbeitnow": lambda: "data" in get("https://www.arbeitnow.com/api/job-board-api", delay=0.5).json(),
    }
    failures = 0
    for name, check in checks.items():
        try:
            ok = check()
        except Exception as e:
            ok, detail = False, str(e)[:80]
        else:
            detail = ""
        print(f"  {name:<10} {'PASS' if ok else 'FAIL ' + detail}")
        failures += 0 if ok else 1
    if cfg["sources"].get("naukri"):
        from jobscout.sources.naukri import parse_search_markdown, scrape_markdown
        md = scrape_markdown("https://www.naukri.com/backend-engineer-jobs?experience=2")
        n = len(parse_search_markdown(md)) if md else 0
        ok = n >= 5
        print(f"  naukri     {'PASS' if ok else 'FAIL'} ({n} tuples via firecrawl)")
        failures += 0 if ok else 1
    sys.exit(1 if failures else 0)


def prospects():
    conn = db.init_db()
    found = discover_prospects()
    added = 0
    for p in found:
        db.upsert_company(conn, p["name"], li_url=p["li_url"],
                          fit_note=p["note"], suitability="good")
        added += 1
    total = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    print(f"[prospects] {added} discovered/updated; companies table now {total}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=SOURCE_MODULES)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--contract-check", action="store_true")
    ap.add_argument("--prospects", action="store_true")
    args = ap.parse_args()
    if args.contract_check:
        contract_check()
    elif args.prospects:
        prospects()
    else:
        run_pipeline(args)
