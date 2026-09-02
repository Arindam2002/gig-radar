"""E2E fixtures: seeded test DB + a real Streamlit server on a throwaway port."""
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from jobscout import db  # noqa: E402
from jobscout.models import Job  # noqa: E402

PORT = 8599


def _iso(days_ago=0, hours_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours_ago)).isoformat()


def _epoch(hours_ago=0):
    return int((datetime.now(timezone.utc) - timedelta(hours=hours_ago)).timestamp())


def seed(db_path: Path):
    conn = db.init_db(db_path)

    def add(title, company, score, matched, *, status="new", salary=(None, None, ""),
            desc="", hours_ago=2, url=None, **extra):
        job = Job(source="linkedin", title=title, company=company,
                  url=url or f"https://x/{title.replace(' ', '-')}", location="Bengaluru, India",
                  description=desc, salary_min=salary[0], salary_max=salary[1],
                  salary_currency=salary[2],
                  posted_at=_iso(hours_ago=hours_ago),
                  extra={"posted_at_epoch": _epoch(hours_ago), **extra})
        db.upsert(conn, job, score, matched)
        jid = db.job_id(company, title, "Bengaluru, India")
        if status != "new":
            db.set_status(conn, jid, status)
        return jid

    # high-score fresh match with disclosed salary + matched skills
    add("AI Infrastructure Engineer", "Sarvam AI", 87,
        ["llm", "inference", "python", "gpu"],
        salary=(35, 45, "INR"),
        desc="Own our vLLM inference stack on GPU clusters. Python, FastAPI, "
             "Prometheus observability, Kubernetes autoscaling.")
    # low-score row (filtered out at default min score 40)
    add("Desktop Support Engineer", "TechSupport Inc", 5, [], desc="L1 support role")
    # undisclosed salary row
    add("LLM Platform Engineer", "Glean", 78, ["llm", "python"],
        desc="Build the ML platform: model serving, embeddings, RAG.")
    # aged applied row -> must appear in the follow-up queue
    aged = add("Backend Engineer", "Razorpay", 70, ["python", "kafka"],
               desc="Payments backend", status="applied")
    conn.execute("UPDATE jobs SET status_updated_at=? WHERE id=?", (_iso(days_ago=9), aged))
    # shortlisted row with contact for the outreach tab
    add("GenAI Engineer", "Fractal", 74, ["llm", "rag"],
        desc="GenAI solutions with vLLM inference and fine-tuning pipelines",
        status="shortlisted", contact_email="hr@fractal.ai",
        contact_email_source="found in posting", company_li_url="https://in.linkedin.com/company/fractal")
    # abroad role with relocation offer
    add("Senior Backend Engineer (Berlin)", "N26", 72, ["python", "kafka"],
        salary=(90000, 110000, "USD"),
        desc="Backend engineering in Berlin. Visa sponsorship and relocation "
             "assistance provided for international candidates.",
        abroad=True, relocation=True, location_restriction="Germany")
    # company prospect
    db.upsert_company(conn, "Pixxel", li_url="https://in.linkedin.com/company/pixxel",
                      fit_note="Space-tech AI startup, strong infra team", suitability="good")
    conn.commit()
    conn.close()


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e")
    db_path = root / "e2e.db"
    seed(db_path)
    study = root / "study"
    (study / "topics").mkdir(parents=True)
    (study / "STUDY.md").write_text("# Study Base\n\ntest study base\n")
    (study / "topics" / "demo-alpha-topic.md").write_text(
        "# Demo Alpha Topic\n\n## Concept\ntest\n")
    (study / "topics" / "demo-beta-topic.md").write_text(
        "# Demo Beta Topic\n\n## Concept\ntest\n")
    briefs = root / "briefs"
    briefs.mkdir()
    (briefs / "TODAY.md").write_text(
        f"# 📋 Daily brief - test\n\n"
        f"**Apply sprint:**\n\n"
        f"- **87** [AI Infrastructure Engineer](https://x/AI-Infrastructure-Engineer) - "
        f"Sarvam AI · ₹35–45 LPA - *Why you: vLLM stack match.*\n"
        f"**2. [LLM Platform Engineer — Glean](https://x/LLM-Platform-Engineer)** · "
        f"growth · numbered-bold format\n"
        f"- **60** [Unknown External Job](https://elsewhere.example/job/123) - "
        f"NotInDb Corp - *no buttons expected*\n\n"
        f"**3. Desktop Support - separate-line link format**\n"
        f"[Job posting](https://x/Desktop-Support-Engineer)\n\n"
        f"**4. Manifest-only pick** - [details](https://moved.example/xyz)\n")
    (briefs / "TODAY.picks.json").write_text(json.dumps([
        {"url": "https://moved.example/xyz", "title": "GenAI Engineer",
         "company": "Fractal"}]))
    env = dict(os.environ, JOBSCOUT_DB=str(db_path), JOBSCOUT_DISABLE_REFRESH="1",
               JOBSCOUT_NO_LLM="1", JOBSCOUT_BRIEFS=str(briefs),
               JOBSCOUT_STUDY=str(study))
    proc = subprocess.Popen(
        [str(ROOT / ".venv/bin/python"), "-m", "streamlit", "run",
         str(ROOT / "dashboard.py"), "--server.port", str(PORT),
         "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            with socket.create_connection(("localhost", PORT), timeout=1):
                break
        except OSError:
            time.sleep(0.5)
    else:
        proc.kill()
        raise RuntimeError("streamlit did not start")
    yield {"url": f"http://localhost:{PORT}", "db_path": db_path}
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture()
def dash(page, server):
    # pages have real URLs now (st.navigation); tests start on Fresh matches
    page.goto(server["url"] + "/fresh")
    page.wait_for_selector("text=Fresh matches", timeout=30000)
    # let Streamlit finish its websocket render
    page.wait_for_timeout(1500)
    return page


def goto_page(page, server, path):
    page.goto(server["url"] + path)
    page.wait_for_timeout(1800)


def db_conn(server):
    return db.connect(server["db_path"])


def job_status(server, title):
    conn = db_conn(server)
    row = conn.execute("SELECT status FROM jobs WHERE title=?", (title,)).fetchone()
    conn.close()
    return row["status"] if row else None
