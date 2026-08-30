"""RemoteOK — flat ~100-item feed, first element is a legal notice.

Salary fields are 0 in ~98% of records (verified); title-only keyword filter
because tags are noise.
"""
from bs4 import BeautifulSoup

from ..models import Job
from ..normalize import to_epoch, to_iso_utc
from .base import classify_market, get, query_match

API = "https://remoteok.com/api"


def fetch(cfg: dict) -> list[Job]:
    try:
        r = get(API, delay=1.0)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [remoteok] failed: {e}")
        return []
    jobs: list[Job] = []
    for d in data:
        try:
            if not isinstance(d, dict) or "legal" in d:
                continue  # first element is the legal notice
            title = d.get("position", "") or d.get("title", "")
            if not query_match(title, cfg["search"]["queries"]):
                continue
            restriction = d.get("location", "")
            abroad = bool(restriction) and classify_market(restriction, cfg) == "abroad"
            lo = d.get("salary_min") or None
            hi = d.get("salary_max") or None
            lo = lo if (lo and lo > 0) else None
            hi = hi if (hi and hi > 0) else (lo if lo else None)
            desc = BeautifulSoup(d.get("description", "") or "", "html.parser").get_text(" ", strip=True)
            jobs.append(Job(
                source="remoteok",
                title=title,
                company=d.get("company", ""),
                url=d.get("url", ""),
                location="Remote",
                remote=True,
                description=desc,
                salary_min=lo, salary_max=hi,
                salary_currency="USD" if lo else "",
                posted_at=to_iso_utc(d.get("date")),
                skills=(d.get("tags") or [])[:10],
                extra={"posted_at_epoch": to_epoch(d.get("date")),
                       "location_restriction": restriction or "unrestricted",
                       "abroad": abroad},
            ))
        except Exception as e:
            print(f"  [remoteok] skipping malformed record: {e}")
    print(f"  [remoteok] {len(jobs)} jobs after filters")
    return jobs
