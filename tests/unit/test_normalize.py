import time

import pytest

from jobscout.normalize import parse_experience, parse_salary, to_epoch, to_iso_utc

# The devils-advocate corpus: every rev-1 failure pinned, plus standard formats.
CORPUS = [
    # rev-1 passing cases (must stay green)
    ("₹15-25 Lacs P.A.", (15, 25, "INR")),
    ("$90k-$120k", (90000, 120000, "USD")),
    ("12,00,000 - 20,00,000 PA", (12, 20, "INR")),
    # rev-1 catastrophic: parsed as ₹1.5-2 LPA
    ("USD 150,000 - 200,000 PA", (150000, 200000, "USD")),
    # rev-1: hourly read as annual
    ("$60 - $80 per hour", (124800, 166400, "USD_CONTRACT")),
    ("$120 - $170 /hour", (249600, 353600, "USD_CONTRACT")),
    # rev-1 misses
    ("175k - 190k", (175000, 190000, "USD")),
    ("Rs 20,00,000 - 30,00,000 per annum", (20, 30, "INR")),
    ("8,00,000 - 12,00,000 P.A.", (8, 12, "INR")),
    ("₹5L - 8L", (5, 8, "INR")),
    ("₹1,00,00,000 - 1,50,00,000 P.A.", (100, 150, "INR")),
    ("$120,000", (120000, 120000, "USD")),
    ("$150,000/yr", (150000, 150000, "USD")),
    # rev-1 accidental match via "pa" in "package" - now via Indian commas
    ("Salary package 5,00,000 - 7,00,000", (5, 7, "INR")),
    # unsupported currencies must be flagged, not scored as undisclosed
    ("€70,000 - €90,000", (None, None, "UNSUPPORTED")),
    ("£80,000", (None, None, "UNSUPPORTED")),
    ("CA$90,000", (None, None, "UNSUPPORTED")),
    # standard formats
    ("25-30 Lacs PA", (25, 30, "INR")),
    ("₹ 32,50,000 - 40,00,000 P.A.", (32.5, 40, "INR")),
    ("40 LPA", (40, 40, "INR")),
    ("3-6 Lacs P.A.", (3, 6, "INR")),
    ("10-15 Lacs", (10, 15, "INR")),
    ("1 Cr", (100, 100, "INR")),
    ("₹50,000 per month", (6, 6, "INR")),
    ("1.5 Lakh per month", (18, 18, "INR")),
    ("$31.2k - $52k", (31200, 52000, "USD")),
    ("$85K", (85000, 85000, "USD")),
    # undisclosed / junk must be empty
    ("Not Disclosed by Recruiter", (None, None, "")),
    ("Competitive", (None, None, "")),
    ("Negotiable", (None, None, "")),
    ("", (None, None, "")),
    ("Best in industry", (None, None, "")),
    # clamps: absurd values rejected rather than trusted
    ("₹500 Lacs P.A.", (None, None, "")),
    ("$5 - $8 per hour", (10400, 16640, "USD_CONTRACT")),  # low but valid; salary filter handles it
    # "rs" inside "years" is not a currency marker
    ("2-5 years experience", (None, None, "")),
]


@pytest.mark.parametrize("text,expected", CORPUS, ids=[c[0][:30] or "empty" for c in CORPUS])
def test_parse_salary(text, expected):
    lo, hi, cur = parse_salary(text)
    exp_lo, exp_hi, exp_cur = expected
    assert cur == exp_cur, f"{text!r}: currency {cur!r} != {exp_cur!r}"
    if exp_lo is None:
        assert lo is None and hi is None, f"{text!r}: expected no value, got {lo}-{hi}"
    else:
        assert lo == pytest.approx(exp_lo), f"{text!r}: min {lo} != {exp_lo}"
        assert hi == pytest.approx(exp_hi), f"{text!r}: max {hi} != {exp_hi}"


def test_to_epoch_formats_all_sortable():
    now = time.time()
    cases = {
        "iso_offset": "2026-08-22T16:00:21+00:00",
        "iso_z": "2026-08-22T16:00:21Z",
        "iso_naive": "2026-08-22T16:00:21",
        "epoch_int": 1787560211,
        "epoch_str": "1787560211",
        "epoch_ms": 1787560211000,
        "relative": "2 hours ago",
    }
    results = {k: to_epoch(v) for k, v in cases.items()}
    for k, v in results.items():
        assert isinstance(v, int), f"{k} -> {v}"
        assert 1e9 < v < 4e9, f"{k} out of range: {v}"
    assert results["iso_offset"] == results["iso_z"] == results["iso_naive"]
    assert results["epoch_int"] == results["epoch_str"] == results["epoch_ms"]
    assert abs(results["relative"] - (now - 7200)) < 60


def test_to_epoch_garbage():
    assert to_epoch(None) is None
    assert to_epoch("") is None
    assert to_epoch("soon") is None
    assert to_epoch("123") is None  # not a plausible epoch


def test_parse_experience():
    assert parse_experience("2-5 years of experience") == (2, 5)
    assert parse_experience("0-2 yrs") == (0, 2)
    assert parse_experience("3+ years") == (3, None)
    assert parse_experience("minimum 4 years in backend") == (4, None)
    assert parse_experience("Experience: 2+ Years") == (2, None)
    assert parse_experience("5 years of experience required") == (5, None)
    assert parse_experience("no requirement stated") == (None, None)
    assert parse_experience("") == (None, None)
    # implausible values rejected
    assert parse_experience("99 years experience") == (None, None)


def test_to_iso_utc_roundtrip():
    assert to_iso_utc(1787560211) == "2026-08-24T08:30:11+00:00"
    assert to_iso_utc("garbage") == ""
