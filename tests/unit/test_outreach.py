import yaml
from pathlib import Path

from jobscout.outreach import (cold_mail_suitability, draft_email,
                               draft_speculative, extract_emails, guess_domain)

ROOT = Path(__file__).resolve().parent.parent.parent

import pytest

if not (ROOT / "profile.yaml").exists():
    pytest.skip("these tests assert against the repo owner's filled profile.yaml "
                "(gitignored) — copy profile.example.yaml and tune expectations "
                "to your own profile", allow_module_level=True)


PROFILE = yaml.safe_load((ROOT / "profile.yaml").read_text())


def test_extract_emails_from_jd():
    text = ("Interested candidates can share resume at hr@acme.com or "
            "call 98765. Also cc talent.team@acme.co.in. Ignore noreply@naukri.com "
            "and image@2x.png style junk.")
    emails = extract_emails(text)
    assert emails[0] == "hr@acme.com"
    assert "talent.team@acme.co.in" in emails
    assert all("noreply" not in e and "naukri" not in e for e in emails)


def test_extract_emails_empty():
    assert extract_emails("") == []
    assert extract_emails("no emails here") == []


def test_suitability():
    assert cold_mail_suitability("Sarvam AI") == "good"
    assert cold_mail_suitability("Infosys Limited") == "low"
    assert cold_mail_suitability("Tata Consultancy Services") == "low"
    assert cold_mail_suitability("Wipro Technologies", boost_list=["wipro"]) == "good"


def test_guess_domain():
    assert guess_domain("Pixxel Private Limited") == "pixxel.com"
    assert guess_domain("") == ""


def test_draft_email_personalizes():
    d = draft_email(PROFILE, {
        "title": "LLM Engineer", "company": "Glean",
        "matched_skills": ["llm", "inference", "python"],
        "description": "vllm serving and gpu inference"})
    assert "LLM Engineer" in d["subject"] and "Glean" in d["subject"]
    assert "llm, inference, python" in d["body"]
    assert "vLLM" in d["body"]  # llm highlight chosen
    assert "Arindam" in d["body"]
    assert len(d["body"].split()) < 200


def test_draft_email_backend_highlight():
    d = draft_email(PROFILE, {
        "title": "Backend Engineer", "company": "Razorpay",
        "matched_skills": ["sql", "auth"], "description": "payments APIs"})
    assert "multi-tenant auth" in d["body"]


def test_draft_speculative():
    d = draft_speculative(PROFILE, "Pixxel", "Space-tech AI startup")
    assert "Pixxel" in d["subject"] and "Pixxel" in d["body"]
    assert "don't see a specific opening" in d["body"]
    assert "Space-tech AI startup" in d["body"]
