"""Company-tier classification: enterprise / growth / startup / staffing / unknown.

Two layers, cached forever in the companies table (each company classified once):
  1. Heuristics — curated name lists + agency patterns, free and instant.
  2. Gemini batch classification (flash-lite) for the rest — one call covers
     ~40 companies, leaning on the model's world knowledge.
"""
import json
import re

TIERS = ["enterprise", "growth", "startup", "staffing", "unknown"]

_ENTERPRISE = re.compile(
    r"\b(microsoft|google|amazon|apple|meta|netflix|nvidia|intel|amd|qualcomm|arm|"
    r"micron|samsung|sony|ibm|oracle|sap|salesforce|adobe|cisco|dell|hp|vmware|"
    r"broadcom|uber|airbnb|spotify|stripe|paypal|intuit|servicenow|workday|atlassian|"
    r"shopify|twilio|datadog|snowflake|databricks|gitlab|booking|agoda|walmart|target|"
    r"jpmorgan|barclays|hsbc|ubs|citi\b|wells fargo|goldman|morgan stanley|blackrock|"
    r"fidelity|american express|amex|mastercard|visa\b|capital one|deutsche bank|"
    r"general motors|ford|tesla|bosch|siemens|philips|honeywell|ge \b|airbus|boeing|"
    r"astrazeneca|pfizer|novartis|roche|thomson reuters|bloomberg|teradata|hitachi|"
    r"accenture|tcs|tata consultancy|infosys|wipro|cognizant|capgemini|hcl|"
    r"ltimindtree|mphasis|ust\b|quest global|epam|globallogic|thoughtworks|"
    r"publicis sapient|luxoft|virtusa|persistent|coforge|hexaware|nagarro|"
    r"flipkart|paytm|aldi|expedia|mastercard|deloitte|kpmg|pwc|ey\b)\b", re.I)

_GROWTH = re.compile(
    r"\b(razorpay|phonepe|cred\b|zerodha|groww|meesho|swiggy|zomato|zepto|ola\b|"
    r"freshworks|zoho|postman|browserstack|chargebee|clevertap|moengage|whatfix|"
    r"hasura|innovaccer|darwinbox|lenskart|nykaa|upstox|jupiter|navi\b|dream11|"
    r"sharechat|inmobi|unacademy|physics ?wallah|perplexity|glean|anthropic|openai|"
    r"mistral|cohere|hugging ?face|scale ai|anyscale|modal\b|replicate|together ai|"
    r"sarvam|krutrim|whatnot|notion|figma|canva|rippling|deel\b|gusto|brex|ramp\b|"
    r"plaid|chime|nubank|wise\b|revolut|n26\b|klarna|instacart|doordash)\b", re.I)

_STAFFING = re.compile(
    r"staffing|recruit(ing|ment|er)?\b|talent\b|placement|manpower|consultanc|"
    r"hr solutions|workforce|outsourc|hiring for|\bjobs?\b.*\bportal\b|"
    r"\b(talentgigs|zigsaw|talentxo|talent500|hudson|bayone|hays\b|randstad|adecco|"
    r"quess|teamlease|kelly services|robert half|michael page|uplers|turing\b|"
    r"toptal|crossover|andela|e-?hireo|gigsaw)\b", re.I)


def heuristic_tier(company: str) -> str:
    """'' when the heuristics don't know — caller escalates to Gemini."""
    name = company or ""
    if _STAFFING.search(name):
        return "staffing"
    if _ENTERPRISE.search(name):
        return "enterprise"
    if _GROWTH.search(name):
        return "growth"
    return ""


_BATCH_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "tier": {"type": "string", "enum": TIERS},
        },
        "required": ["name", "tier"],
    },
}


def gemini_classify(names: list[str], api_key: str, model: str) -> dict[str, str]:
    """Batch-classify company names; returns {name: tier}. Raises on API failure."""
    from .llm import generate
    listing = "\n".join(f"- {n}" for n in names)
    prompt = f"""Classify each company for a software-engineer job seeker in India.

Tiers:
- enterprise: large established company (MNC, public, or 1000+ employees, incl. big IT-services firms)
- growth: funded scale-up or unicorn with a real product and roughly 100+ employees
- startup: early-stage (roughly seed/Series A, under ~100 employees)
- staffing: recruiting/staffing agency or hiring marketplace posting on behalf of clients
- unknown: you genuinely don't recognize it

Companies:
{listing}

Return the JSON array, one entry per company, names copied exactly."""
    out = generate(prompt, api_key, model=model, json_schema=_BATCH_SCHEMA, timeout=90)
    result = {}
    for item in json.loads(out):
        tier = item.get("tier", "unknown")
        result[item.get("name", "")] = tier if tier in TIERS else "unknown"
    return result


def classify_companies(conn, cfg, batch_cap: int = 2, batch_size: int = 40) -> dict:
    """Fill missing tiers: heuristics first, then Gemini for the remainder.
    Returns counts. Companies already classified are never re-sent."""
    rows = conn.execute(
        "SELECT id, name FROM companies WHERE tier IS NULL OR tier=''").fetchall()
    counts = {"heuristic": 0, "gemini": 0, "pending": 0}
    unknown = []
    for r in rows:
        tier = heuristic_tier(r["name"])
        if tier:
            conn.execute("UPDATE companies SET tier=? WHERE id=?", (tier, r["id"]))
            counts["heuristic"] += 1
        else:
            unknown.append(r)
    conn.commit()

    key = cfg.get("keys", {}).get("gemini_api_key")
    model = cfg.get("llm", {}).get("extract_model", "gemini-3.1-flash-lite")
    if key:
        for i in range(0, min(len(unknown), batch_cap * batch_size), batch_size):
            batch = unknown[i:i + batch_size]
            try:
                tiers = gemini_classify([r["name"] for r in batch], key, model)
            except Exception as e:
                print(f"  [tiers] gemini batch failed: {str(e)[:100]}")
                break
            for r in batch:
                tier = tiers.get(r["name"], "unknown")
                conn.execute("UPDATE companies SET tier=? WHERE id=?", (tier, r["id"]))
                counts["gemini"] += 1
            conn.commit()
    counts["pending"] = len(unknown) - counts["gemini"]
    return counts
