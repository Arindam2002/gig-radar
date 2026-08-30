"""Normalization: salary text → comparable numbers, timestamps → sortable epochs.

Salary rules (order matters — each documented failure below was a rev-1 bug):
  1. Detect pay period first: hourly → ×2080 (flagged contract), monthly → ×12.
  2. USD/EUR/GBP patterns run before naked-digit INR ("USD 150,000 PA" must not
     parse as ₹1.5 LPA).
  3. INR needs an anchored marker (lpa/lakh/₹/rs/p.a./per annum) — "pa" inside
     "package" must not count.
  4. Sanity clamps: INR 1–200 LPA, USD $10K–$1M; out-of-range → rejected.
INR results are in LPA; USD annual dollars; EUR/GBP → currency 'UNSUPPORTED'.
"""
import re
from datetime import datetime, timedelta, timezone

# ── timestamps ──────────────────────────────────────────────────────

# "2 hours ago", "3+ weeks ago" (Naukri), "1 day ago"
_REL = re.compile(r"(\d+)\+?\s*(minute|hour|day|week|month)s?\s+ago", re.I)


def to_epoch(value) -> int | None:
    """Best-effort convert epoch int/str, ISO strings, or '2 hours ago' to epoch seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e12:  # milliseconds
            v /= 1000
        return int(v) if 1e9 < v < 4e9 else None
    s = str(value).strip()
    if re.fullmatch(r"\d{10,13}", s):
        return to_epoch(int(s))
    m = _REL.search(s)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta = {"minute": timedelta(minutes=n), "hour": timedelta(hours=n),
                 "day": timedelta(days=n), "week": timedelta(weeks=n),
                 "month": timedelta(days=30 * n)}[unit]
        return int((datetime.now(timezone.utc) - delta).timestamp())
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


def to_iso_utc(value) -> str:
    epoch = to_epoch(value)
    if epoch is None:
        return ""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# ── experience ──────────────────────────────────────────────────────

_EXP_RANGE = re.compile(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I)
_EXP_PLUS = re.compile(r"(\d{1,2})\s*\+\s*(?:years?|yrs?)", re.I)
_EXP_MIN = re.compile(r"(?:minimum|min\.?|at least)\s*(?:of\s*)?(\d{1,2})\s*(?:years?|yrs?)", re.I)
_EXP_BARE = re.compile(r"(\d{1,2})\s*(?:years?|yrs?)\s*(?:of\s*)?(?:experience|exp\b)", re.I)


def parse_experience(text: str) -> tuple[int | None, int | None]:
    """Extract (min_years, max_years) from '2-5 years', '3+ Yrs', 'minimum 4 years'."""
    if not text:
        return None, None
    m = _EXP_RANGE.search(text)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return (lo, hi) if lo <= hi <= 30 else (None, None)
    m = _EXP_PLUS.search(text) or _EXP_MIN.search(text) or _EXP_BARE.search(text)
    if m:
        v = int(m.group(1))
        return (v, None) if v <= 30 else (None, None)
    return None, None


# ── salary ──────────────────────────────────────────────────────────

_HOURLY = re.compile(r"(/\s*h(ou)?r|per\s+hour|hourly|an\s+hour)\b", re.I)
_MONTHLY = re.compile(r"(/\s*mo(nth)?\b|per\s+month|monthly|p\.?m\.?\b)", re.I)

_NUM = r"(\d{1,3}(?:[,.]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
_RANGE_SEP = r"\s*(?:-|–|—|to)\s*"

# anchored: "pa" inside "package" and "rs" inside "years" must NOT match
_INR_MARK = (r"(?:\b(?:lpa|lacs?|lakhs?|inr|crores?|cr)\b|\bper\s+annum\b"
             r"|\bp\.?\s?a\.?(?!\w)|\brs\.?(?!\w)|₹)")
# 2-digit middle comma groups (5,00,000) are uniquely Indian — implicit INR
_INDIAN_COMMAS = re.compile(r"\d{1,2},\d{2},\d{3}")

_LAKH_RANGE = re.compile(
    rf"(?:₹|rs\.?\s*|inr\s*)?{_NUM}\s*(?:lacs?|lakhs?|lpa|l)?{_RANGE_SEP}"
    rf"{_NUM}\s*(lacs?|lakhs?|lpa|l)\b", re.I)
_LAKH_SINGLE = re.compile(rf"(?:₹|rs\.?\s*|inr\s*)?{_NUM}\s*(lacs?|lakhs?|lpa)\b", re.I)
_CR = re.compile(rf"(?:₹|rs\.?\s*|inr\s*)?{_NUM}\s*(?:cr|crores?)\b", re.I)

# rupee amounts written in full, e.g. 12,00,000 - 20,00,000 (needs an INR marker nearby)
_RUPEE_FULL = re.compile(rf"(\d{{1,3}}(?:,\d{{2,3}})+){_RANGE_SEP}(\d{{1,3}}(?:,\d{{2,3}})+)")
# one comma group is enough: annual sub-lakh values die at the 1-200 LPA clamp,
# while monthly "₹50,000 per month" survives via the ×12
_RUPEE_SINGLE = re.compile(r"(\d{1,3}(?:,\d{2,3})+)")
_INR_ANY = re.compile(rf"{_INR_MARK}", re.I)

_USD_MARK = re.compile(r"\$|usd\b|dollars?\b", re.I)
_EUR_GBP = re.compile(r"€|£|\beur\b|\bgbp\b|\bcad\b|ca\$", re.I)

_K_RANGE = re.compile(rf"(?:\$|usd\s*)?{_NUM}\s*k?{_RANGE_SEP}(?:\$|usd\s*)?{_NUM}\s*k\b", re.I)
_K_SINGLE = re.compile(rf"(?:\$|usd\s*){_NUM}\s*k\b", re.I)
_USD_FULL_RANGE = re.compile(rf"(?:\$|usd\s*){_NUM}{_RANGE_SEP}(?:\$|usd\s*)?{_NUM}", re.I)
_USD_FULL_SINGLE = re.compile(rf"(?:\$|usd\s*){_NUM}", re.I)
_BARE_K_RANGE = re.compile(rf"\b(\d{{2,3}})\s*k{_RANGE_SEP}(\d{{2,3}})\s*k\b", re.I)


def _f(s: str) -> float:
    return float(s.replace(",", ""))


def _clamp_inr(lo, hi):
    if lo is None or not (1 <= lo <= 200 and 1 <= hi <= 200 and lo <= hi):
        return None, None, ""
    return lo, hi, "INR"


def _clamp_usd(lo, hi, contract=False):
    if lo is None or not (10_000 <= lo <= 1_000_000 and 10_000 <= hi <= 1_000_000 and lo <= hi):
        return None, None, ""
    return lo, hi, "USD" + ("_CONTRACT" if contract else "")


def parse_salary(text: str) -> tuple[float | None, float | None, str]:
    """Returns (min, max, currency). INR in LPA; USD annual dollars.

    currency: 'INR' | 'USD' | 'USD_CONTRACT' (converted hourly rate) |
              'UNSUPPORTED' (EUR/GBP/CAD) | '' (unparsed/undisclosed)
    """
    if not text:
        return None, None, ""
    t = text.strip()
    if re.search(r"not\s+disclosed|competitive|negotiable", t, re.I):
        return None, None, ""

    # CA$ contains "$", so the unsupported-currency check must run first
    if _EUR_GBP.search(t):
        return None, None, "UNSUPPORTED"

    hourly = bool(_HOURLY.search(t))
    monthly = bool(_MONTHLY.search(t))
    mult = 2080 if hourly else (12 if monthly else 1)

    has_usd = bool(_USD_MARK.search(t))
    has_inr = (bool(_INR_ANY.search(t)) or bool(_INDIAN_COMMAS.search(t))) and not has_usd

    # USD before INR: "USD 150,000 - 200,000 PA" contains both markers
    if has_usd:
        m = _K_RANGE.search(t)
        if m and "k" in m.group(0).lower():
            lo, hi = _f(m.group(1)), _f(m.group(2))
            lo, hi = (lo * 1000 if lo < 1000 else lo), (hi * 1000 if hi < 1000 else hi)
            return _clamp_usd(lo * (mult if hourly else 1), hi * (mult if hourly else 1), hourly)
        m = _USD_FULL_RANGE.search(t)
        if m:
            lo, hi = _f(m.group(1)) * mult, _f(m.group(2)) * mult
            return _clamp_usd(lo, hi, hourly)
        m = _K_SINGLE.search(t)
        if m:
            v = _f(m.group(1)) * 1000
            return _clamp_usd(v, v)
        m = _USD_FULL_SINGLE.search(t)
        if m:
            v = _f(m.group(1)) * mult
            return _clamp_usd(v, v, hourly)
        return None, None, ""

    if has_inr:
        m = _CR.search(t)
        if m:
            v = _f(m.group(1)) * 100  # 1 crore = 100 lakh
            return _clamp_inr(v, v)
        lakh_mult = 12 if monthly else 1
        m = _LAKH_RANGE.search(t)
        if m:
            return _clamp_inr(_f(m.group(1)) * lakh_mult, _f(m.group(2)) * lakh_mult)
        m = _LAKH_SINGLE.search(t)
        if m:
            v = _f(m.group(1)) * lakh_mult
            return _clamp_inr(v, v)
        m = _RUPEE_FULL.search(t)
        if m:
            lo, hi = _f(m.group(1)) * lakh_mult, _f(m.group(2)) * lakh_mult
            return _clamp_inr(lo / 100000, hi / 100000)
        m = _RUPEE_SINGLE.search(t)
        if m:
            v = _f(m.group(1)) * lakh_mult
            return _clamp_inr(v / 100000, v / 100000)
        return None, None, ""

    # no currency marker at all: bare "175k - 190k" is USD by convention
    m = _BARE_K_RANGE.search(t)
    if m:
        return _clamp_usd(_f(m.group(1)) * 1000, _f(m.group(2)) * 1000)
    # bare Indian-format rupee range like "12,00,000 - 20,00,000 PA" is handled
    # above only with a marker; without any marker we refuse to guess.
    return None, None, ""


def salary_display(min_v, max_v, currency, raw="") -> str:
    if min_v is not None and min_v != min_v:  # NaN from pandas
        min_v = None
    if min_v is None:
        if currency == "UNSUPPORTED":
            return raw or "non-USD"
        return raw or "—"
    if currency == "INR":
        if min_v == max_v:
            return f"₹{min_v:g} LPA"
        return f"₹{min_v:g}–{max_v:g} LPA"
    suffix = " (contract)" if currency == "USD_CONTRACT" else ""
    if min_v == max_v:
        return f"${min_v/1000:g}K/yr{suffix}"
    return f"${min_v/1000:g}K–{max_v/1000:g}K/yr{suffix}"
