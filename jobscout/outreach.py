"""Contact discovery + email drafting. Drafts only — nothing is ever sent.

Contact layers (all publicly-sourced):
  1. extract_emails: hiring emails pasted into JD text (automatic at ingest)
  2. find_hr_profiles: Firecrawl web search for public HR/TA profiles
  3. careers_page_emails: Firecrawl scrape of the company careers/contact page
  4. hunter_domain_search: optional free-tier email-pattern lookup
"""
import json
import re
import subprocess

import requests

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# obvious non-contact addresses
_JUNK = re.compile(r"noreply|no-reply|donotreply|example\.|sentry|@naukri|@linkedin|"
                   r"support@|privacy@|legal@|unsubscribe|\.png$|\.jpg$", re.I)
_HIRING_HINT = re.compile(r"hr@|career|job|talent|recruit|hiring|apply", re.I)


def extract_emails(text: str) -> list[str]:
    """Emails from JD text, hiring-related first, junk removed."""
    found = []
    for e in EMAIL_RE.findall(text or ""):
        e = e.strip(".")
        if _JUNK.search(e) or e in found:
            continue
        found.append(e)
    return sorted(found, key=lambda e: 0 if _HIRING_HINT.search(e) else 1)


def _firecrawl(args: list[str], timeout: int = 90) -> str:
    from .settings import firecrawl_cmd
    proc = subprocess.run([*firecrawl_cmd(), *args],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:300])
    return proc.stdout


def _search_items(raw: str) -> list[dict]:
    """Firecrawl search --json returns {"success":..,"data":{"web":[...]}}."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return data
    inner = data.get("data", data)
    if isinstance(inner, dict):
        return inner.get("web") or inner.get("results") or []
    return inner if isinstance(inner, list) else []


def find_hr_profiles(company: str, limit: int = 5) -> list[dict]:
    """Public HR/TA LinkedIn profiles via Firecrawl web search (search results
    only — no LinkedIn scraping, no logins)."""
    query = f'"{company}" (recruiter OR "talent acquisition" OR "HR manager") site:linkedin.com/in'
    out = _firecrawl(["search", query, "--limit", str(limit), "--json"])
    results = []
    items = _search_items(out)
    if not items:
        for m in re.finditer(r"(https://[a-z]{2,3}\.linkedin\.com/in/[^\s\)\]]+)", out):
            items.append({"url": m.group(1), "title": ""})
    for item in items:
        url = item.get("url", "")
        if "linkedin.com/in/" not in url:
            continue
        title = item.get("title", "")
        name = re.split(r"\s*[-–|]\s*", title)[0].strip() if title else ""
        results.append({"name": name, "url": url.split("?")[0]})
    return results[:limit]


def careers_page_emails(company_website: str) -> list[str]:
    """Scrape the careers/contact page for published hiring emails."""
    emails: list[str] = []
    base = company_website.rstrip("/")
    for path in ("/careers", "/contact", ""):
        try:
            md = _firecrawl(["scrape", base + path])
        except (RuntimeError, subprocess.TimeoutExpired):
            continue
        emails.extend(e for e in extract_emails(md) if e not in emails)
        if emails:
            break
    return emails


def hunter_domain_search(domain: str, api_key: str) -> dict:
    r = requests.get("https://api.hunter.io/v2/domain-search",
                     params={"domain": domain, "api_key": api_key, "limit": 5},
                     timeout=25)
    r.raise_for_status()
    d = r.json().get("data", {})
    return {
        "pattern": d.get("pattern") or "",
        "emails": [{"email": e.get("value", ""),
                    "name": f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip(),
                    "position": e.get("position") or ""}
                   for e in d.get("emails", [])],
    }


def guess_domain(company: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "", (company or "").lower()
                  .replace("private", "").replace("limited", "").replace("pvt", "")
                  .replace("ltd", "").replace("technologies", "").replace("labs", ""))
    return f"{slug}.com" if slug else ""


# ── cold-mail suitability ───────────────────────────────────────────

_MNC = re.compile(r"\b(infosys|tcs|tata consultancy|wipro|cognizant|accenture|capgemini|"
                  r"hcl|ibm|oracle|sap|deloitte|ey |kpmg|pwc|amazon|google|microsoft|"
                  r"meta|apple|mastercard|visa|jpmorgan|goldman|morgan stanley|walmart|"
                  r"expedia|dell|cisco|intel|qualcomm|adobe|salesforce|paypal)\b", re.I)


def cold_mail_suitability(company: str, boost_list: list[str] | None = None) -> str:
    """'good' (startup/product, likely responsive) | 'low' (MNC/ATS-only)."""
    if boost_list and any(b.lower() in company.lower() for b in boost_list):
        return "good"
    return "low" if _MNC.search(company or "") else "good"


# ── drafting ────────────────────────────────────────────────────────

def _pick_highlight(profile: dict, matched: list[str], description: str) -> str:
    h = profile.get("highlights", {})
    text = f"{' '.join(matched)} {description}".lower()
    if any(k in text for k in ("llm", "inference", "gpu", "vllm", "fine-tuning",
                               "quantization", "rag", "agents", "pytorch")):
        return h.get("llm", "")
    if any(k in text for k in ("observability", "reliability", "sre", "incident")):
        return h.get("reliability", "")
    if any(k in text for k in ("scale", "kafka", "kubernetes", "platform")):
        return h.get("scale", "")
    return h.get("backend", "")


def draft_email(profile: dict, job: dict) -> dict:
    """job: dict with title, company, matched_skills (list), description."""
    matched = job.get("matched_skills") or []
    top = ", ".join(matched[:3]) if matched else "backend and AI infrastructure"
    highlight = _pick_highlight(profile, matched, job.get("description", ""))
    name = profile.get("name", "")
    subject = f"{job['title']} @ {job['company']} — {name}, {profile.get('experience_years', 2)} YoE backend/AI infra"
    body = f"""Hi{{name}},

I came across the {job['title']} opening at {job['company']} and it lines up closely with what I do: {top}.

{highlight}

I'm currently a Software Engineer at Gothia Digital Solutions (remote, Sweden-based team) exploring my next role. I'd love to be considered for this position — resume attached. Happy to share more or do a quick call whenever convenient.

Best,
{name}
{profile.get('email', '')} | {profile.get('phone', '')}
"""
    return {"subject": subject, "body": body}


def draft_speculative(profile: dict, company: str, fit_note: str = "") -> dict:
    h = profile.get("highlights", {})
    name = profile.get("name", "")
    subject = f"Backend/AI-infra engineer ({profile.get('experience_years', 2)} YoE) — open to opportunities at {company}"
    fit_line = f" {fit_note.strip().rstrip('.')}." if fit_note else ""
    body = f"""Hi{{name}},

I've been following {company} and really like what you're building.{fit_line} I don't see a specific opening that matches, but I wanted to reach out directly in case there's a fit now or soon.

I'm a backend engineer focused on AI infrastructure. {h.get('llm', '')} {h.get('scale', '')}

If there's a team where this profile could help, I'd love to talk — resume attached.

Best,
{name}
{profile.get('email', '')} | {profile.get('phone', '')}
"""
    return {"subject": subject, "body": body}


# ── Gemini-powered drafting (optional) ──────────────────────────────
# A free-tier API key from https://aistudio.google.com works — the Google AI
# Pro subscription itself does not include API credits, but the free tier is
# more than enough for drafting.

def draft_email_gemini(profile: dict, job: dict, api_key: str,
                       model: str = "gemini-3.6-flash") -> dict:
    """Personalized draft via Gemini; raises on API failure (caller falls back)."""
    from .llm import generate
    highlights = "\n".join(f"- {v}" for v in profile.get("highlights", {}).values())
    jd = (job.get("description") or "")[:4000]
    prompt = f"""Write a short cold email (max 130 words) from a job applicant to a recruiter/hiring manager.

Applicant: {profile.get('name')}, {profile.get('experience_years')} years experience, {profile.get('headline')}.
Proven achievements:
{highlights}

Target: the "{job['title']}" role at {job['company']}.
Job description excerpt:
{jd}

Rules: professional but warm, no flattery, no buzzwords, reference 1-2 SPECIFIC
requirements from the JD and connect them to the applicant's concrete achievements
(keep the real numbers). End asking for consideration/a short call; mention resume
attached. Greeting must be exactly "Hi{{name}}," on its own line. Sign off with the
applicant's name, email {profile.get('email')} and phone {profile.get('phone')}.
Return ONLY: first line "Subject: <subject>", blank line, then the body."""
    text = generate(prompt, api_key, model=model, timeout=60).strip()
    subject, body = "", text
    if text.lower().startswith("subject:"):
        first, _, rest = text.partition("\n")
        subject = first[8:].strip()
        body = rest.strip()
    return {"subject": subject or draft_email(profile, job)["subject"], "body": body}


# ── prospect discovery ──────────────────────────────────────────────

# LinkedIn company pages give clean names ("Company | LinkedIn") + canonical URLs
PROSPECT_QUERIES = [
    'site:linkedin.com/company "AI infrastructure" India startup',
    'site:linkedin.com/company "LLM" inference platform startup',
    'site:linkedin.com/company generative AI startup India hiring',
    'site:linkedin.com/company AI startup India series A backend',
]


def discover_prospects(limit_per_query: int = 8) -> list[dict]:
    """Firecrawl web search for suitable companies; returns [{name, li_url, note}]."""
    prospects: list[dict] = []
    seen: set[str] = set()
    for q in PROSPECT_QUERIES:
        try:
            out = _firecrawl(["search", q, "--limit", str(limit_per_query), "--json"])
            items = _search_items(out)
        except Exception as e:
            print(f"  [prospects] '{q[:40]}' failed: {e}")
            continue
        for item in items:
            url = (item.get("url", "") or "").split("?")[0]
            if "linkedin.com/company/" not in url or url in seen:
                continue
            seen.add(url)
            title = item.get("title", "") or ""
            name = re.split(r"\s*[|–-]\s*(?:LinkedIn.*)?$", title)[0].strip() or title[:60]
            note = (item.get("description", "") or "")[:250]
            prospects.append({"name": name, "li_url": url, "note": note})
    return prospects
