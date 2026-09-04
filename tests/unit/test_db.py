import time

from jobscout import db
from jobscout.models import Job


def make_job(**kw):
    base = dict(source="linkedin", title="Backend Engineer", company="Acme",
                url="https://li.example/jobs/view/1", location="Bangalore")
    base.update(kw)
    return Job(**base)


def fresh(tmp_path):
    return db.init_db(tmp_path / "t.db")


def test_multi_city_postings_stay_distinct(tmp_path):
    conn = fresh(tmp_path)
    for i, city in enumerate(["Bangalore", "Pune", "Hyderabad"]):
        job = make_job(company="TCS", title="Software Engineer", location=city,
                       url=f"https://x/{city}")
        assert db.upsert(conn, job, 50, []) == "new"
    rows = conn.execute("SELECT location, url FROM jobs ORDER BY location").fetchall()
    assert len(rows) == 3
    assert {r["location"] for r in rows} == {"Bangalore", "Pune", "Hyderabad"}
    # each keeps its own apply URL
    assert all(r["location"] in r["url"] for r in rows)


def test_dismissed_row_resurfaces_on_fresh_repost(tmp_path):
    conn = fresh(tmp_path)
    old_epoch = int(time.time()) - 30 * 86400
    job = make_job(extra={"posted_at_epoch": old_epoch})
    db.upsert(conn, job, 50, [])
    jid = conn.execute("SELECT id FROM jobs").fetchone()["id"]
    db.set_status(conn, jid, "dismissed")

    repost = make_job(url="https://li.example/jobs/view/999", posted_at="now",
                      extra={"posted_at_epoch": int(time.time())})
    db.upsert(conn, repost, 88, ["python"], freshness_hours=48)
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["status"] == "new", "fresh re-post must resurface a dismissed row"


def test_dismissed_stays_dismissed_on_same_posting(tmp_path):
    conn = fresh(tmp_path)
    epoch = int(time.time()) - 3600
    db.upsert(conn, make_job(extra={"posted_at_epoch": epoch}), 50, [])
    jid = conn.execute("SELECT id FROM jobs").fetchone()["id"]
    db.set_status(conn, jid, "dismissed")
    db.upsert(conn, make_job(extra={"posted_at_epoch": epoch}), 50, [])
    assert conn.execute("SELECT status FROM jobs").fetchone()["status"] == "dismissed"


def test_url_hash_merges_company_name_variants(tmp_path):
    conn = fresh(tmp_path)
    url = "https://li.example/jobs/view/42"
    db.upsert(conn, make_job(company="Google", url=url), 40, [])
    result = db.upsert(conn, make_job(company="Google India Private Limited", url=url), 40, [])
    assert result == "merged"
    assert conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 1


def test_norm_company_variants_share_id():
    a = db.job_id("PhonePe", "Backend Engineer", "Bangalore")
    b = db.job_id("PhonePe Private Limited", "Backend Engineer", "Bangalore")
    c = db.job_id("PhonePe 4.1 (More Jobs)", "Backend Engineer", "Bangalore")
    assert a == b == c


def test_rows_persist_across_reconnect(tmp_path):
    path = tmp_path / "t.db"
    conn = db.init_db(path)
    db.upsert(conn, make_job(), 50, [])
    conn.close()
    conn2 = db.connect(path)
    assert conn2.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 1


def test_posted_at_epoch_sorts_mixed_sources(tmp_path):
    conn = fresh(tmp_path)
    now = int(time.time())
    for i, (src, age) in enumerate([("linkedin", 7200), ("himalayas", 86400),
                                    ("naukri", 600), ("remoteok", 200000)]):
        db.upsert(conn, make_job(source=src, title=f"Job {i}", url=f"https://x/{i}",
                                 extra={"posted_at_epoch": now - age}), 50, [])
    rows = conn.execute(
        "SELECT source FROM jobs ORDER BY posted_at_epoch DESC").fetchall()
    assert [r["source"] for r in rows] == ["naukri", "linkedin", "himalayas", "remoteok"]


def test_merge_rescore_and_url_priority(tmp_path):
    conn = fresh(tmp_path)
    url = "https://x/shared"
    db.upsert(conn, make_job(source="linkedin", url=url, description=""), 12, [])
    naukri = make_job(source="naukri", url=url, description="python vllm fastapi",
                      salary_min=35, salary_max=45, salary_currency="INR")
    db.upsert(conn, naukri, 75, ["python"],
              rescore=lambda t, d, s: (75.0, ["python", "vllm"]))
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["score"] == 75.0
    assert row["url_source"] == "naukri"
    assert row["salary_min"] == 35
    assert "vllm" in row["matched_skills"]
    assert row["needs_detail"] == 0


def test_merge_ors_remote_flag(tmp_path):
    conn = fresh(tmp_path)
    url = "https://x/j"
    db.upsert(conn, make_job(url=url, remote=False), 50, [])
    db.upsert(conn, make_job(url=url, remote=True), 50, [])
    assert conn.execute("SELECT remote FROM jobs").fetchone()["remote"] == 1


def test_concurrent_write_during_pipeline(tmp_path):
    """A dashboard click must succeed while a pipeline run is upserting.

    upsert() commits per call, so writers only contend briefly; WAL +
    busy_timeout must absorb that without 'database is locked'.
    """
    import threading

    path = tmp_path / "t.db"
    conn_pipeline = db.init_db(path)
    db.upsert(conn_pipeline, make_job(), 50, [])
    jid = conn_pipeline.execute("SELECT id FROM jobs").fetchone()["id"]

    errors = []

    def pipeline():
        conn = db.connect(path)
        try:
            for i in range(200):
                db.upsert(conn, make_job(title=f"Job {i}", url=f"https://x/{i}"), 50, [])
        except Exception as e:  # pragma: no cover
            errors.append(e)

    t = threading.Thread(target=pipeline)
    t.start()
    conn_ui = db.connect(path)
    for _ in range(20):
        db.set_status(conn_ui, jid, "shortlisted")  # must not raise
    t.join()
    assert not errors
    assert conn_ui.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0] == "shortlisted"


def test_migration_adds_columns(tmp_path):
    path = tmp_path / "t.db"
    conn = db.init_db(path)
    conn.execute("ALTER TABLE jobs DROP COLUMN seen_at")
    conn.commit()
    conn.close()
    conn = db.init_db(path)  # re-init migrates
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert "seen_at" in cols


def test_set_status_records_event(tmp_path):
    conn = fresh(tmp_path)
    db.upsert(conn, make_job(), 50, [])
    jid = conn.execute("SELECT id FROM jobs").fetchone()["id"]
    db.set_status(conn, jid, "applied")
    db.set_status(conn, jid, "replied")
    events = conn.execute(
        "SELECT status FROM status_events WHERE ref_id=? ORDER BY id", (jid,)).fetchall()
    assert [e["status"] for e in events] == ["applied", "replied"]


def test_archive_backlog(tmp_path):
    conn = fresh(tmp_path)
    now = int(time.time())

    def add(title, score, epoch=None, status="new", thin=False):
        # a real JD by default: a description-less (thin) card carries only a
        # title-derived provisional score and must survive until enriched
        db.upsert(conn, make_job(title=title, url=f"https://x/{title}",
                                 extra={"posted_at_epoch": epoch or now - 3600}),
                  score, [])
                                 description="" if thin else "x" * 400,
        if status != "new":
            jid = db.job_id("Acme", title, "Bangalore")
            db.set_status(conn, jid, status)

    add("Junk Role", 10)                                  # junk -> archived
    add("Marginal Old", 30, epoch=now - 6 * 86400)        # low+old -> archived
    add("Marginal Fresh", 30)                             # low but fresh -> kept
    add("Thin Junk", 10, thin=True)                       # unread card -> kept
    add("Good Fresh", 80)                                 # kept
    add("Expired Good", 80, epoch=now - 12 * 86400)       # posted too old -> archived
    add("Applied Junk", 10, status="applied")             # tracked -> NEVER touched
    n = db.archive_backlog(conn)
    assert n == 3
    statuses = {r["title"]: r["status"] for r in conn.execute("SELECT title, status FROM jobs")}
    assert statuses["Junk Role"] == "archived"
    assert statuses["Marginal Old"] == "archived"
    assert statuses["Expired Good"] == "archived"
    assert statuses["Marginal Fresh"] == "new"
    assert statuses["Good Fresh"] == "new"
    assert statuses["Applied Junk"] == "applied"

    assert statuses["Thin Junk"] == "new"

def test_archive_stale(tmp_path):
    conn = fresh(tmp_path)
    db.upsert(conn, make_job(), 50, [])
    conn.execute("UPDATE jobs SET fetched_at='2026-07-01T00:00:00+00:00'")
    conn.commit()
    assert db.archive_stale(conn, days=14) == 1
    assert conn.execute("SELECT status FROM jobs").fetchone()["status"] == "archived"


def test_companies_and_contacts(tmp_path):
    conn = fresh(tmp_path)
    cid = db.upsert_company(conn, "Pixxel", fit_note="space AI startup")
    cid2 = db.upsert_company(conn, "Pixxel Private Limited", domain="pixxel.space")
    assert cid == cid2
    row = conn.execute("SELECT * FROM companies").fetchone()
    assert row["fit_note"] == "space AI startup" and row["domain"] == "pixxel.space"
    db.add_contact(conn, company_id=cid, name="A", url="https://li/in/a", source="firecrawl")
    db.add_contact(conn, company_id=cid, name="A", url="https://li/in/a", source="firecrawl")
    assert conn.execute("SELECT COUNT(*) c FROM contacts").fetchone()["c"] == 1


def test_study_progress(tmp_path):
    conn = fresh(tmp_path)
    assert db.study_progress_map(conn) == {}
    db.set_study_done(conn, "llm-serving", True)
    m = db.study_progress_map(conn)
    assert "llm-serving" in m and m["llm-serving"]
    first = m["llm-serving"]
    db.set_study_done(conn, "llm-serving", True)  # re-tick refreshes timestamp
    assert db.study_progress_map(conn)["llm-serving"] >= first
    db.set_study_done(conn, "llm-serving", False)
    assert db.study_progress_map(conn) == {}
