"""Arbeitnow - mostly on-site German-language roles (verified 91%).

Kept on a tight leash: remote==true AND English title AND query match.
Expected yield ~4 jobs/run; drop the source if it stays useless.
"""
from bs4 import BeautifulSoup

from ..models import Job
from ..normalize import to_epoch, to_iso_utc
from .base import get, looks_english, query_match

API = "https://www.arbeitnow.com/api/job-board-api"


def fetch(cfg: dict) -> list[Job]:
    try:
        r = get(API, delay=1.0)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [arbeitnow] failed: {e}")
        return []
    jobs: list[Job] = []
    for d in data.get("data", []):
        try:
            title = d.get("title", "")
            if not d.get("remote"):
                continue
            if not looks_english(title):
                continue
            if not query_match(title, cfg["search"]["queries"]):
                continue
            desc = BeautifulSoup(d.get("description", "") or "", "html.parser").get_text(" ", strip=True)
            jobs.append(Job(
                source="arbeitnow",
                title=title,
                company=d.get("company_name", ""),
                url=d.get("url", ""),
                location=d.get("location", "") or "Remote",
                remote=True,
                description=desc,
                posted_at=to_iso_utc(d.get("created_at")),
                skills=(d.get("tags") or [])[:10],
                extra={"posted_at_epoch": to_epoch(d.get("created_at"))},
            ))
        except Exception as e:
            print(f"  [arbeitnow] skipping malformed record: {e}")
    print(f"  [arbeitnow] {len(jobs)} jobs after filters")
    return jobs
