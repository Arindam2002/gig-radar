"""JSearch (RapidAPI, Google for Jobs) — free tier ~200 req/mo.

Capped at 1 call per query per run to stay inside the free tier.
Off until a key is configured.
"""
from ..models import Job
from ..normalize import parse_salary, to_epoch, to_iso_utc
from .base import get

API = "https://jsearch.p.rapidapi.com/search"


def fetch(cfg: dict) -> list[Job]:
    key = cfg.get("keys", {}).get("jsearch_rapidapi_key")
    if not key:
        print("  [jsearch] no key configured — skipping")
        return []
    headers = {"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
    jobs: list[Job] = []
    for query in cfg["search"]["queries"]:
        try:
            r = get(API, params={"query": f"{query} in India", "date_posted": "3days",
                                 "num_pages": 1}, headers=headers, delay=1.5)
            r.raise_for_status()
            results = r.json().get("data", [])
        except Exception as e:
            print(f"  [jsearch] '{query}' failed: {e}")
            continue
        for d in results:
            try:
                lo, hi = d.get("job_min_salary"), d.get("job_max_salary")
                cur = (d.get("job_salary_currency") or "").upper()
                if lo and cur == "INR":
                    lo, hi = round(lo / 100000, 1), round((hi or lo) / 100000, 1)
                elif lo and cur == "USD":
                    hi = hi or lo
                else:
                    lo, hi, cur = parse_salary(str(d.get("job_salary") or ""))
                city = d.get("job_city") or ""
                country = d.get("job_country") or ""
                jobs.append(Job(
                    source="jsearch",
                    title=d.get("job_title", ""),
                    company=d.get("employer_name", ""),
                    url=d.get("job_apply_link", ""),
                    location=", ".join(x for x in (city, country) if x),
                    remote=bool(d.get("job_is_remote")),
                    description=d.get("job_description", ""),
                    salary_min=lo, salary_max=hi, salary_currency=cur if lo else "",
                    posted_at=to_iso_utc(d.get("job_posted_at_timestamp")),
                    extra={"posted_at_epoch": to_epoch(d.get("job_posted_at_timestamp"))},
                ))
            except Exception as e:
                print(f"  [jsearch] skipping malformed record: {e}")
        print(f"  [jsearch] '{query}': {len(results)} jobs")
    return jobs
