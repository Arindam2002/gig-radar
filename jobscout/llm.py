"""Gemini structured extraction (optional - needs keys.gemini_api_key).

Used to pull structured facts out of JD text that regex can't reach:
experience requirement, salary, seniority, India eligibility. JSON mode with a
response schema, so outputs are machine-parseable by contract.

NOTE: older model aliases (gemini-2.5-*) 404 for newly-created keys; the
default model is overridable via config llm.model.
"""
import json
import re

import requests


class RateLimited(Exception):
    """429 from the API. .retry_seconds is Google's suggested wait;
    .quota_info names the exact quota that was exhausted."""

    def __init__(self, retry_seconds: float, quota_info: str):
        self.retry_seconds = retry_seconds
        self.quota_info = quota_info
        super().__init__(f"rate limited ({quota_info}), retry in {retry_seconds:.0f}s")

DEFAULT_MODEL = "gemini-3.6-flash"
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "exp_min": {"type": "integer", "nullable": True,
                    "description": "minimum years of experience required, null if not stated"},
        "exp_max": {"type": "integer", "nullable": True},
        "salary_min_lpa": {"type": "number", "nullable": True,
                           "description": "min salary in INR lakhs per annum, null unless INR salary stated"},
        "salary_max_lpa": {"type": "number", "nullable": True},
        "salary_min_usd": {"type": "number", "nullable": True,
                           "description": "min annual USD salary, null unless USD salary stated"},
        "salary_max_usd": {"type": "number", "nullable": True},
        "seniority": {"type": "string",
                      "enum": ["intern", "junior", "mid", "senior", "staff+", "unclear"]},
        "india_eligible": {"type": "boolean",
                           "description": "false ONLY if the posting explicitly restricts to countries excluding India"},
        "visa_or_relocation": {"type": "boolean",
                               "description": "true ONLY if the posting explicitly offers visa sponsorship or relocation assistance"},
    },
    "required": ["seniority", "india_eligible"],
}


def generate(prompt: str, api_key: str, *, model: str = DEFAULT_MODEL,
             json_schema: dict | None = None, timeout: int = 60) -> str:
    body: dict = {"contents": [{"parts": [{"text": prompt}]}]}
    if json_schema:
        body["generationConfig"] = {"responseMimeType": "application/json",
                                    "responseSchema": json_schema}
    r = requests.post(API.format(model=model), params={"key": api_key},
                      json=body, timeout=timeout)
    if r.status_code == 429:
        raise RateLimited(*_parse_429(r))
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def _parse_429(r) -> tuple[float, str]:
    retry, quota = 60.0, "unknown quota"
    try:
        details = r.json()["error"].get("details", [])
        for d in details:
            t = d.get("@type", "")
            if "RetryInfo" in t and d.get("retryDelay"):
                m = re.match(r"([\d.]+)s", d["retryDelay"])
                if m:
                    retry = float(m.group(1))
            if "QuotaFailure" in t:
                quota = "; ".join(
                    f"{v.get('quotaId', v.get('quotaMetric', '?')).split('/')[-1]}"
                    f"={v.get('quotaValue', '?')}"
                    for v in d.get("violations", []))
    except (ValueError, KeyError):
        pass
    return retry, quota


def extract_job_facts(title: str, description: str, api_key: str,
                      model: str = DEFAULT_MODEL) -> dict:
    """Structured facts from a JD. Raises on API failure; caller decides fallback."""
    prompt = f"""Extract facts from this job posting. Use null for anything not stated -
do not guess salary or experience numbers that are not in the text.

Title: {title}
Posting:
{description[:6000]}"""
    out = generate(prompt, api_key, model=model, json_schema=FACTS_SCHEMA)
    facts = json.loads(out)

    def _num(v, lo, hi):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v if lo <= v <= hi else None

    return {
        "exp_min": int(v) if (v := _num(facts.get("exp_min"), 0, 30)) is not None else None,
        "exp_max": int(v) if (v := _num(facts.get("exp_max"), 0, 40)) is not None else None,
        "salary_min_lpa": _num(facts.get("salary_min_lpa"), 1, 200),
        "salary_max_lpa": _num(facts.get("salary_max_lpa"), 1, 200),
        "salary_min_usd": _num(facts.get("salary_min_usd"), 10_000, 1_000_000),
        "salary_max_usd": _num(facts.get("salary_max_usd"), 10_000, 1_000_000),
        "seniority": facts.get("seniority", "unclear"),
        "india_eligible": bool(facts.get("india_eligible", True)),
        "visa_or_relocation": bool(facts.get("visa_or_relocation", False)),
    }
