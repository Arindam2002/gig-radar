"""LinkedIn public logged-out ("guest") endpoints - the India backbone.

Two stages:
  1. fetch(cfg): guest search cards (title/company/location/age/url), cheap.
  2. enrich(url): guest job-view page -> full JD via the JSON-LD block
     (drift-resistant), with a CSS fallback. Called by run.py for the top-N
     candidates only, on a strict budget with 4s delays.

Politeness: any non-200 aborts the source for this run (a 404/429/403 must
never burn the full sleep budget), volumes stay small, no login ever.
"""
import html as html_mod
import json
import re

from bs4 import BeautifulSoup

from ..models import Job
from ..normalize import to_epoch, to_iso_utc
from .base import eligible, get

_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
PAGES_PER_QUERY = 2  # 10 cards per page


def fetch(cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    by_url: dict[str, Job] = {}
    s = cfg["search"]
    seconds = s.get("freshness_hours", 48) * 3600
    for query in s["queries"]:
        for location in ("India", "Remote"):
            for start in range(0, PAGES_PER_QUERY * 10, 10):
                params = {
                    # "Remote" = remote-from-India (f_WT=2): Worldwide-remote
                    # results pin to foreign cities and fail the eligibility
                    # gate, so remote jobs a person in India can take are found
                    # by searching India WITH the remote filter.
                    "keywords": query,
                    "location": "India",
                    "f_TPR": f"r{seconds}",
                    "f_E": "2,3",  # entry / associate - 2 YoE band
                    "start": start,
                }
                if location == "Remote":
                    params["f_WT"] = "2"
                try:
                    r = get(SEARCH, params=params, delay=3.0)
                except Exception as e:
                    print(f"  [linkedin] '{query}' ({location}) request failed: {e}")
                    return jobs
                if r.status_code != 200:
                    print(f"  [linkedin] HTTP {r.status_code} - aborting source for this run")
                    return jobs
                count = _parse_cards(r.text, location == "Remote", jobs, by_url, cfg)
                if count == 0:
                    break  # no more results for this query/location
        print(f"  [linkedin] '{query}': {len(jobs)} total so far")

    # abroad pass: US/EU remote roles at foreign companies (relocation or
    # location-flexible upside). One page per query/region, top queries only.
    if cfg.get("sources", {}).get("linkedin_abroad", True):
        for query in s["queries"][:3]:
            for region in ("United States", "European Union"):
                params = {"keywords": query, "location": region,
                          "f_TPR": f"r{seconds}", "f_E": "2,3",
                          "f_WT": "2", "start": 0}
                try:
                    r = get(SEARCH, params=params, delay=3.0)
                except Exception as e:
                    print(f"  [linkedin abroad] '{query}' ({region}) failed: {e}")
                    return jobs
                if r.status_code != 200:
                    print(f"  [linkedin abroad] HTTP {r.status_code} - stopping abroad pass")
                    return jobs
                n = _parse_cards(r.text, True, jobs, by_url, cfg, force_abroad=True)
                print(f"  [linkedin abroad] '{query}' ({region}): {n} cards")
    return jobs


def _parse_cards(html: str, remote: bool, jobs: list[Job], by_url: dict,
                 cfg: dict, force_abroad: bool = False) -> int:
    soup = BeautifulSoup(html, "html.parser")
    count = 0
    for card in soup.select("li div.base-card"):
        a = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
        title_el = card.select_one("h3")
        company_el = card.select_one("h4")
        if not (a and title_el and company_el):
            continue
        url = a["href"].split("?")[0]
        if url in by_url:
            # same posting seen again - a remote-filtered sighting proves
            # the already-collected job is remote-friendly
            if remote:
                by_url[url].remote = True
            continue
        title_text = title_el.get_text(strip=True)
        if _CJK.search(title_text):
            continue  # non-English listing
        time_el = card.select_one("time")
        loc_el = card.select_one(".job-search-card__location")
        loc_text = loc_el.get_text(strip=True) if loc_el else ""
        # a "remote" card pinned to a foreign city (NY, Berlin, Singapore…) is
        # country-restricted remote at a foreign company - tag abroad, keep it
        abroad = force_abroad or (remote and not eligible(loc_text, cfg))
        rel = time_el.get_text(strip=True) if time_el else ""
        epoch = to_epoch(rel)
        job = Job(
            source="linkedin",
            title=title_text,
            company=company_el.get_text(strip=True),
            url=url,
            location=loc_text or ("Remote" if remote else "India"),
            remote=remote,
            posted_at=to_iso_utc(epoch) if epoch else "",
            extra={"posted_at_epoch": epoch,
                   "location_restriction": loc_text if remote else "",
                   "abroad": abroad},
        )
        by_url[url] = job
        jobs.append(job)
        count += 1
    return count


# ── stage 2: detail enrichment ──────────────────────────────────────

def enrich(url: str, fetch_html=None) -> dict:
    """Fetch a guest job-view page; return enrichment fields (possibly empty).

    fetch_html: injectable page fetcher (used for the Firecrawl fallback and
    for offline fixture tests). Default is a polite direct GET.
    """
    if fetch_html is None:
        def fetch_html(u):
            r = get(u, delay=4.0)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            return r.text
    try:
        html = fetch_html(url)
    except Exception as e:
        return {"error": str(e)}
    if "authwall" in html[:2000]:
        return {"error": "authwall"}
    return parse_detail(html)


def parse_detail(html: str) -> dict:
    out: dict = {}
    soup = BeautifulSoup(html, "html.parser")

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if data.get("@type") != "JobPosting":
            continue
        desc_html = data.get("description", "")
        if desc_html:
            # JSON-LD descriptions arrive entity-escaped (&lt;p&gt;…), so
            # unescape before stripping tags
            desc_html = html_mod.unescape(desc_html)
            out["description"] = BeautifulSoup(desc_html, "html.parser").get_text(" ", strip=True)
        if data.get("employmentType"):
            out["employment_type"] = str(data["employmentType"])
        org = data.get("hiringOrganization") or {}
        if isinstance(org, dict) and org.get("sameAs"):
            out["company_li_url"] = org["sameAs"]
        if data.get("datePosted"):
            epoch = to_epoch(data["datePosted"])
            if epoch:
                out["posted_at_epoch"] = epoch
        break

    if "description" not in out:
        markup = soup.select_one("div.show-more-less-html__markup, div.description__text")
        if markup:
            out["description"] = markup.get_text(" ", strip=True)

    seniority = soup.find(string=re.compile(r"Seniority level", re.I))
    if seniority:
        sib = seniority.find_next("span")
        if sib:
            out["seniority"] = sib.get_text(strip=True)
    return out
