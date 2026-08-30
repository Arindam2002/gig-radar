import json

from jobscout import db, tiers


def test_heuristic_tiers():
    assert tiers.heuristic_tier("Capital One") == "enterprise"
    assert tiers.heuristic_tier("NVIDIA Corporation") == "enterprise"
    assert tiers.heuristic_tier("Infosys Limited") == "enterprise"
    assert tiers.heuristic_tier("Razorpay Software") == "growth"
    assert tiers.heuristic_tier("Sarvam AI") == "growth"
    assert tiers.heuristic_tier("Talentgigs") == "staffing"
    assert tiers.heuristic_tier("Hudson Manpower") == "staffing"
    assert tiers.heuristic_tier("ABC Recruiting Solutions") == "staffing"
    assert tiers.heuristic_tier("Some Tiny Shop") == ""  # -> escalates to Gemini


def test_classify_companies_flow(tmp_path, monkeypatch):
    conn = db.init_db(tmp_path / "t.db")
    db.upsert_company(conn, "Capital One", status="tracked")
    db.upsert_company(conn, "Mystery Labs", status="tracked")
    monkeypatch.setattr(tiers, "gemini_classify",
                        lambda names, k, m: {n: "startup" for n in names})
    counts = tiers.classify_companies(conn, {"keys": {"gemini_api_key": "x"}, "llm": {}})
    assert counts["heuristic"] == 1 and counts["gemini"] == 1
    got = {r["name"]: r["tier"] for r in conn.execute("SELECT name, tier FROM companies")}
    assert got["Capital One"] == "enterprise"
    assert got["Mystery Labs"] == "startup"
    # second pass: nothing left to classify (cached forever)
    counts2 = tiers.classify_companies(conn, {"keys": {"gemini_api_key": "x"}, "llm": {}})
    assert counts2 == {"heuristic": 0, "gemini": 0, "pending": 0}


def test_classify_no_key_leaves_pending(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    db.upsert_company(conn, "Mystery Labs", status="tracked")
    counts = tiers.classify_companies(conn, {"keys": {}, "llm": {}})
    assert counts["pending"] == 1


def test_gemini_classify_parses(monkeypatch):
    from jobscout import llm
    payload = [{"name": "A", "tier": "growth"}, {"name": "B", "tier": "bogus"}]
    monkeypatch.setattr(llm, "generate", lambda *a, **k: json.dumps(payload))
    out = tiers.gemini_classify(["A", "B"], "key", "model")
    assert out == {"A": "growth", "B": "unknown"}


def test_tracked_promoted_to_prospect(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    cid = db.upsert_company(conn, "Acme", status="tracked")
    db.upsert_company(conn, "Acme", status="prospect")
    assert conn.execute("SELECT status FROM companies WHERE id=?", (cid,)).fetchone()[0] == "prospect"
