#!/usr/bin/env python3
"""Morning brief: refresh + one bounded daily action plan + study drop.

  python brief.py               refresh, then write briefs/YYYY-MM-DD.md (+ TODAY.md)
  python brief.py --no-fetch    brief from existing data only

Designed for cron (see README). The brief is deliberately BOUNDED: ~10 jobs,
follow-ups due, one prospect — a 30-minute sprint, then study, done for the day.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from jobscout import db  # noqa: E402
from jobscout.db import norm_company  # noqa: E402
from jobscout.normalize import salary_display  # noqa: E402
from jobscout.study import (daily_prep_md, gap_report_md, job_track,  # noqa: E402
                            GOOD_TIERS)

from jobscout.settings import briefs_dir, load_configs as _load_configs  # noqa: E402

BRIEFS = briefs_dir()


def _sal(r):
    return salary_display(r["salary_min"], r["salary_max"], r["salary_currency"],
                          r["salary_text"])


def pick_top_jobs(conn, n_per_track: int = 4, n_abroad: int = 2) -> dict:
    """Balanced picks: AI-track + backend/.NET-track + abroad, preferring
    enterprise/growth/unclassified companies (staffing excluded from the brief)."""
    tiers = {norm_company(r["name"]): (r["tier"] or "unknown")
             for r in conn.execute("SELECT name, tier FROM companies")}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=4)).timestamp()
    rows = conn.execute(
        """SELECT * FROM jobs WHERE status='new' AND score >= 45
           AND (posted_at_epoch IS NULL OR posted_at_epoch >= ?)
           ORDER BY score DESC LIMIT 150""", (cutoff,)).fetchall()

    def tier_of(r):
        return tiers.get(norm_company(r["company"]), "unknown")

    usable = [r for r in rows if tier_of(r) in GOOD_TIERS]
    buckets = {"ai": [], "backend": [], "abroad": []}
    for r in usable:
        if r["abroad"]:
            buckets["abroad"].append(r)
            continue
        track = job_track(json.loads(r["matched_skills"] or "[]"))
        buckets["ai" if track == "ai" else "backend"].append(r)
    return {
        "AI / LLM-infra": buckets["ai"][:n_per_track],
        "Backend / .NET": buckets["backend"][:n_per_track],
        "Abroad 🌍": sorted(buckets["abroad"], key=lambda r: (-r["relocation"], -r["score"]))[:n_abroad],
    }


def followups_due(conn):
    now = datetime.now(timezone.utc)
    return conn.execute(
        """SELECT title, company, status, status_updated_at, url FROM jobs
           WHERE (status='applied' AND status_updated_at < ?)
              OR (status='outreach' AND status_updated_at < ?)
           ORDER BY status_updated_at ASC LIMIT 10""",
        ((now - timedelta(days=7)).isoformat(),
         (now - timedelta(days=4)).isoformat())).fetchall()


def next_prospect(conn):
    return conn.execute(
        """SELECT name, fit_note, li_url FROM companies
           WHERE status='prospect' AND suitability='good'
           ORDER BY created_at DESC LIMIT 1""").fetchone()


def build_brief(conn, cfg, profile) -> str:
    now = datetime.now(timezone.utc)
    today = now.strftime("%A, %d %b %Y")
    lines = [f"# 📋 Daily brief — {today}", ""]

    picks = pick_top_jobs(conn)
    total = sum(len(v) for v in picks.values())
    lines += [f"**Apply sprint ({total} jobs, ~30 min, then you're done):**", ""]
    for section, rows in picks.items():
        if not rows:
            continue
        lines.append(f"### {section}")
        for r in rows:
            reloc = " ✈️" if r["relocation"] else ""
            exp = f" · {int(r['exp_min'])}+ yrs" if r["exp_min"] is not None else ""
            lines.append(f"- **{r['score']:.0f}** [{r['title']}]({r['url']}) — "
                         f"{r['company']} · {_sal(r)}{exp}{reloc}")
        lines.append("")

    fus = followups_due(conn)
    if fus:
        lines.append("### 🔔 Follow-ups due")
        for r in fus:
            days = (now - datetime.fromisoformat(r["status_updated_at"])).days
            lines.append(f"- [{r['title']}]({r['url']}) — {r['company']} "
                         f"(_{r['status']} {days}d ago, nudge them_)")
        lines.append("")

    p = next_prospect(conn)
    if p:
        link = f" ([hiring team]({p['li_url'].rstrip('/')}/people/))" if p["li_url"] else ""
        lines += ["### 🎯 One cold-mail prospect",
                  f"- **{p['name']}** — {p['fit_note'] or 'good fit'}{link}", ""]

    # study drop
    key = cfg.get("keys", {}).get("gemini_api_key")
    lines += ["---", "", "# 📚 Today's prep", ""]
    if key:
        try:
            model = cfg.get("llm", {}).get("extract_model", "gemini-3.1-flash-lite")
            lines.append(daily_prep_md(conn, profile, key, model))
        except Exception as e:
            lines.append(f"_Prep generation failed ({str(e)[:80]}) — see gap report below._")
    else:
        lines.append("_Add a Gemini key in config.yaml to get a generated daily prep drop._")

    # weekly gap report (Mondays, or whenever missing)
    if now.weekday() == 0 or not (BRIEFS / "gap_report.md").exists():
        gap = gap_report_md(conn, profile)
        (BRIEFS / "gap_report.md").write_text(gap)
    lines += ["", "---", "", "# 📈 Skill-gap report (weekly)", "",
              (BRIEFS / "gap_report.md").read_text()]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args()
    BRIEFS.mkdir(exist_ok=True)
    if not args.no_fetch:
        subprocess.run([sys.executable, str(ROOT / "run.py")], cwd=ROOT, timeout=1200)
    cfg, profile = _load_configs()
    conn = db.init_db()
    md = build_brief(conn, cfg, profile)
    day_file = BRIEFS / f"{datetime.now(timezone.utc):%Y-%m-%d}.md"
    day_file.write_text(md)
    (BRIEFS / "TODAY.md").write_text(md)
    print(f"brief written: {day_file}")


if __name__ == "__main__":
    main()
