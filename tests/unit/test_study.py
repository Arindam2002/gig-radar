import json

from jobscout import db
from jobscout.models import Job
from jobscout.study import gap_report_md, good_company_jobs, job_track, skill_demand

MINI_PROFILE = {
    "core_skills": {"python": ["python"], "c#": ["c#"], "llm": ["llm", "vllm"]},
    "transferable_skills": {"sql": ["sql"]},
}


def seed(tmp_path):
    conn = db.init_db(tmp_path / "s.db")

    def add(title, company, desc, score=70):
        db.upsert(conn, Job(source="linkedin", title=title, company=company,
                            url=f"https://x/{title}-{company}", location="Bengaluru",
                            description=desc), score, [])

    add("Backend Engineer", "BigCorp",
        "python sql aws system design microservices " * 20)
    add("Platform Engineer", "GrowthCo",
        "c# .net azure aws terraform distributed systems " * 20)
    add(".NET Developer", "StaffAgency",
        "golang golang golang c# " * 30)  # staffing tier -> excluded
    add("AI Engineer", "BigCorp", "python vllm llm inference gpu " * 20)
    add("Low Score Role", "BigCorp", "php wordpress " * 30, score=10)
    db.upsert_company(conn, "BigCorp", tier="enterprise")
    db.upsert_company(conn, "GrowthCo", tier="growth")
    db.upsert_company(conn, "StaffAgency", tier="staffing")
    return conn


def test_good_company_jobs_excludes_staffing_and_low_scores(tmp_path):
    conn = seed(tmp_path)
    jobs = good_company_jobs(conn, min_score=50)
    companies = {j["company"] for j in jobs}
    assert "StaffAgency" not in companies
    assert "BigCorp" in companies and "GrowthCo" in companies
    assert all(j["score"] >= 50 for j in jobs)


def test_skill_demand_counts_and_have_status(tmp_path):
    conn = seed(tmp_path)
    demand = skill_demand(conn, MINI_PROFILE, min_score=50)
    # aws appears in 2/3 good JDs and is not in the profile -> missing
    assert demand["aws"]["have"] == "missing"
    assert demand["aws"]["count"] == 2
    assert demand["python"]["have"] == "core"
    # golang only appears at the staffing agency -> not in demand at all
    assert "golang" not in demand


def test_gap_report_flags_missing_high_demand(tmp_path):
    conn = seed(tmp_path)
    md = gap_report_md(conn, MINI_PROFILE, min_score=50)
    assert "aws" in md
    assert "learn" in md          # missing + high-demand -> learn flag
    assert "staffing" in md.lower() or "excluded" in md.lower()


def test_job_track():
    assert job_track(["llm", "inference", "python"]) == "ai"
    assert job_track(["c#", ".net", "azure"]) == "dotnet"
    assert job_track(["python", "kafka"]) == "backend"
    assert job_track([]) == "backend"


def test_brief_picks_balanced_and_skips_staffing(tmp_path):
    import brief as brief_mod
    conn = seed(tmp_path)
    # tag matched skills + freshness so picks work
    import time
    now = int(time.time())
    conn.execute("UPDATE jobs SET posted_at_epoch=?", (now - 3600,))
    conn.execute("UPDATE jobs SET matched_skills=? WHERE title='AI Engineer'",
                 (json.dumps(["llm", "inference"]),))
    conn.execute("UPDATE jobs SET matched_skills=? WHERE title='Platform Engineer'",
                 (json.dumps(["c#", ".net"]),))
    conn.commit()
    picks = brief_mod.pick_top_jobs(conn)
    all_companies = [r["company"] for rows in picks.values() for r in rows]
    assert "StaffAgency" not in all_companies
    assert any(r["title"] == "AI Engineer" for r in picks["AI / LLM-infra"])
    assert any(r["title"] == "Platform Engineer" for r in picks["Backend / .NET"])
