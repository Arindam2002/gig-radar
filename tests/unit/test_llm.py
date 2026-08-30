import json

from jobscout import llm


def test_extract_job_facts_parses_and_clamps(monkeypatch):
    payload = {"exp_min": 3, "exp_max": 6, "salary_min_lpa": 30, "salary_max_lpa": 45,
               "salary_min_usd": None, "salary_max_usd": None,
               "seniority": "mid", "india_eligible": True}

    monkeypatch.setattr(llm, "generate", lambda *a, **k: json.dumps(payload))
    f = llm.extract_job_facts("Backend Engineer", "some jd", "key")
    assert f["exp_min"] == 3 and f["exp_max"] == 6
    assert f["salary_min_lpa"] == 30 and f["salary_max_lpa"] == 45
    assert f["seniority"] == "mid" and f["india_eligible"] is True


def test_extract_job_facts_rejects_absurd_values(monkeypatch):
    payload = {"exp_min": 99, "exp_max": None, "salary_min_lpa": 900,
               "salary_max_lpa": None, "salary_min_usd": 5,
               "salary_max_usd": None, "seniority": "unclear", "india_eligible": False}
    monkeypatch.setattr(llm, "generate", lambda *a, **k: json.dumps(payload))
    f = llm.extract_job_facts("X", "jd", "key")
    assert f["exp_min"] is None
    assert f["salary_min_lpa"] is None
    assert f["salary_min_usd"] is None
    assert f["india_eligible"] is False


def test_extract_job_facts_missing_keys(monkeypatch):
    monkeypatch.setattr(llm, "generate", lambda *a, **k: "{}")
    f = llm.extract_job_facts("X", "jd", "key")
    assert f["seniority"] == "unclear"
    assert f["india_eligible"] is True  # default: don't hide without evidence
