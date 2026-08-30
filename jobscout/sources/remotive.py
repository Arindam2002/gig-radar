"""Remotive - the public feed is a fixed ~20-job sample that ignores all query
params (verified). One request per run, near-zero expectations, near-zero cost.
"""
from bs4 import BeautifulSoup

from ..models import Job
from ..normalize import parse_salary, to_epoch, to_iso_utc
from .base import classify_market, get, query_match


API = "https://remotive.com/api/remote-jobs"


def fetch(cfg: dict) -> list[Job]:
    try:
        r = get(API, delay=1.0)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [remotive] failed: {e}")
        return []
    jobs: list[Job] = []
    for d in data.get("jobs", []):
        try:
            title = d.get("title", "")
            if not query_match(title, cfg["search"]["queries"]):
                continue
            restriction = d.get("candidate_required_location", "")
            abroad = classify_market(restriction, cfg) == "abroad"
            lo, hi, cur = parse_salary(d.get("salary", ""))
            desc = BeautifulSoup(d.get("description", "") or "", "html.parser").get_text(" ", strip=True)
            jobs.append(Job(
                source="remotive",
                title=title,
                company=d.get("company_name", ""),
                url=d.get("url", ""),
                location="Remote",
                remote=True,
                description=desc,
                salary_text=d.get("salary", ""),
                salary_min=lo, salary_max=hi, salary_currency=cur,
                posted_at=to_iso_utc(d.get("publication_date")),
                skills=d.get("tags", [])[:10],
                extra={"posted_at_epoch": to_epoch(d.get("publication_date")),
                       "location_restriction": restriction or "unrestricted",
                       "abroad": abroad},
            ))
        except Exception as e:
            print(f"  [remotive] skipping malformed record: {e}")
    print(f"  [remotive] {len(jobs)} jobs after filters")
    return jobs
