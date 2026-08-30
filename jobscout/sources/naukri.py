"""Naukri.com via Firecrawl JS rendering.

Naukri's JSON API is recaptcha-blocked (406) and its HTML is a client-side
shell, so we render search pages through Firecrawl (authenticated CLI) and
parse the markdown job tuples:

    ## [Title](https://www.naukri.com/job-listings-... "Title")
    [Company](...-jobs-careers-...) [4.3](ambitionbox) [733 Reviews](...)
    0-2 Yrs4-8 Lacs PAChennai, Bengaluru
    <snippet line>
    - skill bullets
    3+ weeks agosave

Budget-gated by config firecrawl.naukri_queries_per_run.
"""
import re
import subprocess
import tempfile
from pathlib import Path

from ..models import Job
from ..normalize import parse_salary, to_epoch, to_iso_utc
from .base import UA  # noqa: F401  (kept for parity; firecrawl brings its own browser)

_TUPLE_SPLIT = re.compile(r"^## \[", re.M)
_HEAD = re.compile(r"^(.*?)\]\((https://www\.naukri\.com/job-listings-[^\s\)]+)")
_COMPANY = re.compile(r"\[([^\]]+)\]\(https://www\.naukri\.com/[^\)]*-jobs-careers-[^\)]*\)")
_META = re.compile(r"^(\d+-?\d*)\s*Yrs(.*)$", re.M)
_SALARY = re.compile(r"(\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?\s*Lacs?\s*PA|Not disclosed)", re.I)
_AGO = re.compile(r"((?:\d+\+?\s*)?(?:minute|hour|day|week|month)s?\s+ago|just now|today|few hours ago)", re.I)


def _slug(query: str) -> str:
    # Naukri's SEO slugs spell out symbols: dot-net-developer-jobs, c-sharp-…
    q = query.lower().replace(".net", "dot net").replace("c#", "c sharp")
    return re.sub(r"[^a-z0-9]+", "-", q).strip("-")


def scrape_markdown(url: str, timeout: int = 120) -> str:
    """Render a page via the Firecrawl CLI; returns markdown ('' on failure)."""
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
        out = Path(f.name)
    try:
        from ..settings import firecrawl_cmd
        proc = subprocess.run(
            [*firecrawl_cmd(), "scrape", url,
             "--wait-for", "5000", "--country", "IN", "-o", str(out)],
            capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            print(f"  [naukri] firecrawl failed: {proc.stderr.strip()[:200]}")
            return ""
        return out.read_text()
    except subprocess.TimeoutExpired:
        print("  [naukri] firecrawl timed out")
        return ""
    finally:
        out.unlink(missing_ok=True)


def fetch(cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    budget = cfg.get("firecrawl", {}).get("naukri_queries_per_run", 3)
    exp = cfg["search"].get("experience_years", 2)
    for query in cfg["search"]["queries"][:budget]:
        url = f"https://www.naukri.com/{_slug(query)}-jobs?experience={exp}&jobAge=3"
        md = scrape_markdown(url)
        if not md:
            continue
        parsed = parse_search_markdown(md)
        print(f"  [naukri] '{query}': {len(parsed)} jobs")
        jobs.extend(parsed)
    return jobs


def parse_search_markdown(md: str) -> list[Job]:
    jobs: list[Job] = []
    blocks = _TUPLE_SPLIT.split(md)[1:]
    for block in blocks:
        try:
            job = _parse_block(block)
        except Exception as e:
            print(f"  [naukri] skipping malformed tuple: {e}")
            continue
        if job:
            jobs.append(job)
    return jobs


def _parse_block(block: str) -> Job | None:
    head = _HEAD.search(block)
    if not head:
        return None
    title, url = head.group(1), head.group(2)

    m = _COMPANY.search(block)
    company = m.group(1) if m else ""
    company = re.sub(r"^posted by\s+", "", company, flags=re.I)
    if not company:
        return None

    salary_text, location, exp_text = "", "", ""
    meta = _META.search(block)
    if meta:
        exp_text = meta.group(1)
        rest = meta.group(2).strip()
        sm = _SALARY.search(rest)
        if sm:
            salary_text = sm.group(1)
            rest = rest.replace(sm.group(1), "")
        location = rest.strip(" ,")
    lo, hi, cur = parse_salary(salary_text)

    ago = _AGO.search(block)
    epoch = to_epoch(ago.group(1)) if ago else None
    if ago and not epoch and re.search(r"just now|today|few hours ago", ago.group(1), re.I):
        import time
        epoch = int(time.time()) - 3600

    skills = re.findall(r"^- (.+)$", block, re.M)
    snippet = ""
    for line in block.splitlines():
        line = line.strip()
        if (line and not line.startswith(("[", "!", "-", "#"))
                and "Yrs" not in line and "ago" not in line and len(line) > 40):
            snippet = line
            break

    return Job(
        source="naukri",
        title=title.strip(),
        company=company.strip(),
        url=url,
        location=location or "India",
        remote="remote" in location.lower() or "hybrid" in location.lower(),
        description=snippet,
        salary_text=salary_text,
        salary_min=lo, salary_max=hi, salary_currency=cur,
        posted_at=to_iso_utc(epoch) if epoch else "",
        skills=skills[:10],
        extra={"posted_at_epoch": epoch, "experience": exp_text},
    )
