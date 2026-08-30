from pathlib import Path

import yaml

from jobscout.score import score_job

ROOT = Path(__file__).resolve().parent.parent.parent

import pytest

if not (ROOT / "profile.yaml").exists():
    pytest.skip("these tests assert against the repo owner's filled profile.yaml "
                "(gitignored) — copy profile.example.yaml and tune expectations "
                "to your own profile", allow_module_level=True)


PROFILE = yaml.safe_load((ROOT / "profile.yaml").read_text())
JDS = yaml.safe_load((ROOT / "tests/fixtures/labeled_jds.yaml").read_text())
CFG = {"companies": {"blocklist": [], "boost": []}}


def score_fixture(jd):
    s, matched = score_job(jd["title"], jd["description"], [], jd["company"], PROFILE, CFG)
    return s


def by_label(label):
    return {jd["title"]: score_fixture(jd) for jd in JDS if jd["label"] == label}


def test_ranking_dream_above_reject():
    dream, reject = by_label("dream"), by_label("reject")
    assert min(dream.values()) > max(reject.values()), (
        f"ranking inversion: dream={dream} reject={reject}")


def test_dotnet_equal_priority():
    """User decision 2026-08-25: strong .NET roles rank ALONGSIDE AI-infra
    (equal priority), not buried like rev-2 nor above like rev-1."""
    ai_infra = score_fixture(next(j for j in JDS if j["title"] == "AI Infrastructure Engineer"))
    dotnet = score_fixture(next(j for j in JDS if j["title"] == ".NET Software Engineer"))
    assert dotnet >= 65, f".NET role buried at {dotnet}"
    assert abs(ai_infra - dotnet) <= 20, (ai_infra, dotnet)


def test_dream_scores_are_high():
    for title, s in by_label("dream").items():
        assert s >= 60, f"{title} scored only {s}"


def test_reject_scores_are_low():
    for title, s in by_label("reject").items():
        assert s <= 30, f"{title} scored {s}, too high for a reject"


def test_acceptable_in_the_middle():
    dream, acceptable = by_label("dream"), by_label("acceptable")
    # every dream beats the average acceptable
    avg_acc = sum(acceptable.values()) / len(acceptable)
    assert all(s > avg_acc for s in dream.values()), (dream, acceptable)


def test_vllm_matches_llm_concept():
    s, matched = score_job("Engineer", "we serve models with vLLM in production",
                           [], "X", PROFILE, CFG)
    assert "llm" in matched


def test_hyphen_variants_match():
    s, matched = score_job("Engineer", "experience with fine tuning and LoRA adapters",
                           [], "X", PROFILE, CFG)
    assert "fine-tuning" in matched


def test_thin_card_title_driven():
    """Stage-1 LinkedIn cards (no description) rank by title, not near zero."""
    ai, _ = score_job("AI Infrastructure Engineer", "", [], "X", PROFILE, CFG)
    generic, _ = score_job("Software Engineer", "", [], "X", PROFILE, CFG)
    assert ai >= 50, f"thin AI-infra card buried at {ai}"
    assert ai > generic


def test_blocklist_zeroes():
    cfg = {"companies": {"blocklist": ["wipro"], "boost": []}}
    s, _ = score_job("AI Engineer", "vllm python gpu inference", [], "Wipro Ltd", PROFILE, cfg)
    assert s == 0.0


def test_anti_title_penalty():
    s_sp, _ = score_job("SharePoint Developer", "c# asp.net azure", [], "X", PROFILE, CFG)
    s_backend, _ = score_job("Backend Developer", "c# asp.net azure", [], "X", PROFILE, CFG)
    assert s_sp < s_backend


def test_skills_tags_count_without_description():
    """Naukri rows: no long JD but rich skill tags must still score well."""
    s, matched = score_job(
        "Backend Engineer", "",
        ["Python", "FastAPI", "Kubernetes", "Docker", "Kafka"], "X", PROFILE, CFG)
    assert "python" in matched and "fastapi" in matched
    assert s >= 60
