"""Adzuna India - free API (1000 calls/mo). Off until keys are configured."""
from ..models import Job
from ..normalize import to_epoch, to_iso_utc
from .base import get

API = "https://api.adzuna.com/v1/api/jobs/in/search/1"


def fetch(cfg: dict) -> list[Job]:
    keys = cfg.get("keys", {})
    app_id, app_key = keys.get("adzuna_app_id"), keys.get("adzuna_app_key")
    if not (app_id and app_key):
        print("  [adzuna] no keys configured - skipping")
        return []
    jobs: list[Job] = []
    for query in cfg["search"]["queries"]:
        try:
            r = get(API, params={
                "app_id": app_id, "app_key": app_key, "what": query,
                "max_days_old": max(1, cfg["search"].get("freshness_hours", 48) // 24),
                "results_per_page": 20, "sort_by": "date",
            }, delay=1.0)
            r.raise_for_status()
            results = r.json().get("results", [])
        except Exception as e:
            print(f"  [adzuna] '{query}' failed: {e}")
            continue
        for d in results:
            try:
                lo, hi = d.get("salary_min"), d.get("salary_max")
                # Adzuna India reports INR per annum
                lo = round(lo / 100000, 1) if lo else None
                hi = round(hi / 100000, 1) if hi else (lo if lo else None)
                jobs.append(Job(
                    source="adzuna",
                    title=d.get("title", "").replace("<strong>", "").replace("</strong>", ""),
                    company=(d.get("company") or {}).get("display_name", ""),
                    url=d.get("redirect_url", ""),
                    location=", ".join((d.get("location") or {}).get("area", [])[-2:]),
                    description=d.get("description", ""),
                    salary_min=lo, salary_max=hi,
                    salary_currency="INR" if lo else "",
                    posted_at=to_iso_utc(d.get("created")),
                    extra={"posted_at_epoch": to_epoch(d.get("created"))},
                ))
            except Exception as e:
                print(f"  [adzuna] skipping malformed record: {e}")
        print(f"  [adzuna] '{query}': {len(results)} jobs")
    return jobs
