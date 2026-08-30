"""Himalayas — cursor-paginated free API. Newest-first freshness feed.

Corpus is ~100K jobs at a hard 20/page; we take a few pages of the newest and
gate hard on India eligibility (probe found 0/20 India-eligible in a sample).
All fields arrive stringly-typed: 'None', "['United States']", epoch-strings.
"""
from bs4 import BeautifulSoup

from ..models import Job
from ..normalize import to_epoch, to_iso_utc
from .base import classify_market, get, parse_pylist, query_match

API = "https://himalayas.app/jobs/api"
MAX_PAGES = 5


def _num(v):
    try:
        n = float(str(v))
        return n if n > 0 else None
    except (ValueError, TypeError):
        return None


def fetch(cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    queries = cfg["search"]["queries"]
    cursor = None
    for page in range(MAX_PAGES):
        params = {"limit": 20}
        if cursor:
            params["cursor"] = cursor
        try:
            r = get(API, params=params, delay=1.0)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  [himalayas] page {page} failed: {e}")
            break
        for d in data.get("jobs", []):
            try:
                job = _to_job(d, queries, cfg)
            except Exception as e:
                print(f"  [himalayas] skipping malformed record: {e}")
                continue
            if job:
                jobs.append(job)
        cursor = data.get("nextCursor")
        if not cursor:
            break
    print(f"  [himalayas] {len(jobs)} jobs after filters")
    return jobs


def _to_job(d: dict, queries: list[str], cfg: dict) -> Job | None:
    title = d.get("title", "")
    if not query_match(title, queries):
        return None
    restrictions = parse_pylist(d.get("locationRestrictions"))
    restriction_text = ", ".join(str(x) for x in restrictions)
    abroad = classify_market(restriction_text, cfg) == "abroad"

    lo, hi = _num(d.get("minSalary")), _num(d.get("maxSalary"))
    currency = (d.get("currency") or "")
    period = (d.get("salaryPeriod") or "annual").lower()
    if lo and period == "hourly":
        lo, hi = lo * 2080, (hi or lo) * 2080
    if lo and period == "monthly":
        lo, hi = lo * 12, (hi or lo) * 12
    if currency not in ("USD", ""):
        lo = hi = None
        currency = "UNSUPPORTED"
    elif lo:
        currency = "USD"
        hi = hi or lo
    else:
        currency = ""

    desc_html = d.get("description", "") or ""
    description = BeautifulSoup(desc_html, "html.parser").get_text(" ", strip=True)

    return Job(
        source="himalayas",
        title=title,
        company=d.get("companyName", ""),
        url=d.get("applicationLink", "") or d.get("guid", ""),
        location="Remote",
        remote=True,
        description=description,
        salary_min=lo, salary_max=hi, salary_currency=currency,
        posted_at=to_iso_utc(d.get("pubDate")),
        skills=[c.replace("-", " ") for c in parse_pylist(d.get("categories"))][:10],
        extra={
            "posted_at_epoch": to_epoch(d.get("pubDate")),
            "expires_at": to_iso_utc(d.get("expiryDate")),
            "location_restriction": restriction_text or "unrestricted",
            "abroad": abroad,
        },
    )
