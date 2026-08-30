import ast
import re
import time

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})


def get(url: str, *, delay: float = 1.0, **kwargs) -> requests.Response:
    """Polite GET: small delay between requests, sane timeout."""
    time.sleep(delay)
    kwargs.setdefault("timeout", 25)
    return session.get(url, **kwargs)


def parse_pylist(value) -> list:
    """Himalayas ships stringified Python lists ("['United States']") — not JSON."""
    if isinstance(value, list):
        return value
    if not value or value in ("None", "null"):
        return []
    try:
        out = ast.literal_eval(value)
        return out if isinstance(out, list) else []
    except (ValueError, SyntaxError):
        return []


def eligible(restriction_text: str, cfg: dict) -> bool:
    """India-eligibility: unrestricted or open to India/APAC/global."""
    t = (restriction_text or "").strip().lower()
    if not t:
        return True
    allow = cfg.get("eligibility", {}).get("allow", ["worldwide", "india"])
    return any(a in t for a in allow)


def classify_market(restriction_text: str, cfg: dict) -> str:
    """'ok' = doable from India; 'abroad' = foreign-restricted (US/EU/… company
    role — interesting for relocation or if they flex on location)."""
    return "ok" if eligible(restriction_text, cfg) else "abroad"


_RELOC = re.compile(
    r"visa\s+sponsorship|sponsorship\s+(?:is\s+)?(?:available|provided|offered)|"
    r"relocation\s+(?:assistance|support|package|provided|offered|bonus)|"
    r"we\s+sponsor|willing\s+to\s+sponsor|work\s+permit\s+(?:support|sponsorship)|"
    r"help\s+you\s+relocate|relocation\s+is\s+(?:available|possible)", re.I)


_RELOC_NEG = re.compile(r"\b(no|not|cannot|can'?t|unable\s+to|without|don'?t|"
                        r"unfortunately)\b[^.!\n]*$", re.I)


def mentions_relocation(text: str) -> bool:
    """JD explicitly OFFERS visa sponsorship / relocation help ("no visa
    sponsorship available" must not count)."""
    m = _RELOC.search(text or "")
    if not m:
        return False
    prefix = text[max(0, m.start() - 60):m.start()]
    return not _RELOC_NEG.search(prefix)


def query_match(title: str, queries: list[str]) -> bool:
    """Word-boundary keyword match on TITLE ONLY (board tags are noise)."""
    t = (title or "").lower()
    words = {w for q in queries for w in q.lower().split()}
    words -= {"developer", "engineer"}  # too generic alone
    return any(re.search(rf"\b{re.escape(w)}\b", t) for w in words) or \
        bool(re.search(r"\b(backend|back-end|platform|ml|ai|llm|python|infrastructure|sde|software)\b", t))


_ASCII = re.compile(r"[a-zA-Z\s\-/()&,.+#0-9]")


def looks_english(text: str) -> bool:
    """Cheap German/non-English detector for Arbeitnow titles."""
    if not text:
        return False
    if re.search(r"\((m/w/d|w/m/d|m/f/d)\)", text, re.I):
        # the (m/w/d) marker itself is fine — German words are the signal
        pass
    german = re.search(
        r"\b(mitarbeiter|werkstudent|entwickler|berater|kaufmann|fachkraft|"
        r"leiter|gesucht|und|für|bereich)\b|[äöüß]", text, re.I)
    return german is None
