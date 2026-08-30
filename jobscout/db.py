"""SQLite layer.

Identity: sha1(norm_company | norm_title | norm_city) so multi-city postings
stay distinct, plus a unique url_hash so the same posting seen on two sources
merges even when company names differ ("PhonePe" vs "PhonePe Private Limited").

Terminal statuses (dismissed/rejected/archived) never swallow a fresh
re-posting: if a merge arrives with a meaningfully newer posted_at, the row
resurfaces as 'new'.
"""
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import os as _os

DB_PATH = Path(_os.environ.get("JOBSCOUT_DB")
               or Path(__file__).resolve().parent.parent / "jobscout.db")

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    source          TEXT,
    sources         TEXT DEFAULT '[]',
    title           TEXT,
    company         TEXT,
    location        TEXT,
    remote          INTEGER DEFAULT 0,
    url             TEXT,
    url_source      TEXT DEFAULT '',
    url_hash        TEXT,
    description     TEXT DEFAULT '',
    salary_text     TEXT DEFAULT '',
    salary_min      REAL,
    salary_max      REAL,
    salary_currency TEXT DEFAULT '',
    posted_at       TEXT DEFAULT '',
    posted_at_epoch INTEGER,
    fetched_at      TEXT,
    expires_at      TEXT DEFAULT '',
    seen_at         TEXT,
    skills          TEXT DEFAULT '[]',
    matched_skills  TEXT DEFAULT '[]',
    score           REAL DEFAULT 0,
    needs_detail    INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'new',
    status_updated_at TEXT,
    notes           TEXT DEFAULT '',
    contact_name    TEXT DEFAULT '',
    contact_url     TEXT DEFAULT '',
    contact_email   TEXT DEFAULT '',
    contact_email_source TEXT DEFAULT '',
    company_li_url  TEXT DEFAULT '',
    location_restriction TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_posted_epoch ON jobs(posted_at_epoch);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_url_hash ON jobs(url_hash);

CREATE TABLE IF NOT EXISTS companies (
    id              TEXT PRIMARY KEY,
    name            TEXT,
    domain          TEXT DEFAULT '',
    li_url          TEXT DEFAULT '',
    website         TEXT DEFAULT '',
    fit_note        TEXT DEFAULT '',
    suitability     TEXT DEFAULT '',
    status          TEXT DEFAULT 'prospect',
    status_updated_at TEXT,
    notes           TEXT DEFAULT '',
    created_at      TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT,
    company_id      TEXT,
    name            TEXT DEFAULT '',
    url             TEXT DEFAULT '',
    email           TEXT DEFAULT '',
    source          TEXT DEFAULT '',
    created_at      TEXT
);

CREATE TABLE IF NOT EXISTS status_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT DEFAULT 'job',
    ref_id          TEXT,
    status          TEXT,
    at              TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_at ON status_events(at);

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER);
"""

JOB_STATUSES = ["new", "shortlisted", "applied", "outreach", "replied",
                "interview", "offer", "rejected", "dismissed", "archived"]
COMPANY_STATUSES = ["prospect", "outreach", "replied", "conversation", "dropped"]

TERMINAL = {"dismissed", "rejected", "archived"}

# which source's URL to prefer when the same job is seen in several places
URL_PRIORITY = {"naukri": 4, "linkedin": 3, "jsearch": 2, "adzuna": 2}

_SUFFIXES = re.compile(
    r"\b(pvt|private|limited|ltd|llc|inc|corp|corporation|technologies|"
    r"technology|solutions|india|labs|software|services)\b", re.I)
_RATING_NOISE = re.compile(r"\d\.\d|\(\s*more jobs\s*\)|reviews?", re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Create/migrate schema. Called by run.py and test setup — not per-connect."""
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection):
    have_c = {r[1] for r in conn.execute("PRAGMA table_info(companies)")}
    if "tier" not in have_c:
        conn.execute("ALTER TABLE companies ADD COLUMN tier TEXT DEFAULT ''")
    have = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    wanted = {
        "url_source": "TEXT DEFAULT ''", "url_hash": "TEXT",
        "posted_at_epoch": "INTEGER", "expires_at": "TEXT DEFAULT ''",
        "seen_at": "TEXT", "needs_detail": "INTEGER DEFAULT 0",
        "contact_email_source": "TEXT DEFAULT ''",
        "company_li_url": "TEXT DEFAULT ''",
        "location_restriction": "TEXT DEFAULT ''",
        "exp_min": "INTEGER", "exp_max": "INTEGER",
        "seniority": "TEXT DEFAULT ''", "llm_extracted": "INTEGER DEFAULT 0",
        "abroad": "INTEGER DEFAULT 0", "relocation": "INTEGER DEFAULT 0",
    }
    for col, decl in wanted.items():
        if col not in have:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {decl}")
    # one-time backfill: seed the activity history from current statuses so the
    # calendar isn't empty for jobs tracked before status_events existed
    if conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0] == 0:
        conn.execute(
            """INSERT INTO status_events (kind, ref_id, status, at)
               SELECT 'job', id, status, status_updated_at FROM jobs
               WHERE status IN ('shortlisted','applied','outreach','replied',
                                'interview','offer') AND status_updated_at IS NOT NULL""")
        conn.execute(
            """INSERT INTO status_events (kind, ref_id, status, at)
               SELECT 'company', id, status, status_updated_at FROM companies
               WHERE status IN ('outreach','replied','conversation')
               AND status_updated_at IS NOT NULL""")
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
    else:
        conn.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))


def norm_company(name: str) -> str:
    s = _RATING_NOISE.sub(" ", name or "")
    s = _SUFFIXES.sub(" ", s)
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def job_id(company: str, title: str, location: str) -> str:
    city = _norm(location).split(",")[0].strip() if location else ""
    key = f"{norm_company(company)}|{_norm(title)}|{city}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def url_hash(url: str) -> str | None:
    if not url:
        return None
    u = url.split("?")[0].rstrip("/").lower()
    return hashlib.sha1(u.encode()).hexdigest()[:16]


def upsert(conn: sqlite3.Connection, job, score: float, matched: list[str],
           *, freshness_hours: int = 48, rescore=None) -> str:
    """Insert or merge a job sighting. Commits. Returns 'new' | 'merged'.

    rescore: optional callable(title, description, skills_list) -> (score, matched)
    used to refresh the score after a merge changes the row's fields.
    """
    jid = job_id(job.company, job.title, job.location)
    uh = url_hash(job.url)
    now = now_iso()

    row = None
    if uh:
        row = conn.execute("SELECT * FROM jobs WHERE url_hash=?", (uh,)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()

    if row is None:
        conn.execute(
            """INSERT INTO jobs (id, source, sources, title, company, location,
               remote, url, url_source, url_hash, description, salary_text,
               salary_min, salary_max, salary_currency, posted_at,
               posted_at_epoch, fetched_at, expires_at, skills, matched_skills,
               score, needs_detail, status, status_updated_at, contact_name,
               contact_url, contact_email, contact_email_source, company_li_url,
               location_restriction, exp_min, exp_max)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (jid, job.source, json.dumps([job.source]), job.title, job.company,
             job.location, int(job.remote), job.url, job.source, uh,
             job.description, job.salary_text, job.salary_min, job.salary_max,
             job.salary_currency, job.posted_at, job.extra.get("posted_at_epoch"),
             now, job.extra.get("expires_at", ""), json.dumps(job.skills),
             json.dumps(matched), score,
             int(not job.description), "new", now, job.contact_name,
             job.contact_url, job.extra.get("contact_email", ""),
             job.extra.get("contact_email_source", ""),
             job.extra.get("company_li_url", ""),
             job.extra.get("location_restriction", ""),
             job.extra.get("exp_min"), job.extra.get("exp_max")))
        if job.extra.get("abroad") or job.extra.get("relocation"):
            conn.execute("UPDATE jobs SET abroad=?, relocation=? WHERE id=?",
                         (int(bool(job.extra.get("abroad"))),
                          int(bool(job.extra.get("relocation"))), jid))
        conn.commit()
        return "new"

    # ── merge ──
    rid = row["id"]
    sources = set(json.loads(row["sources"] or "[]")) | {job.source}
    updates: dict = {"sources": json.dumps(sorted(sources)), "fetched_at": now}

    # a sighting via a remote-filtered search proves the job is remote-friendly
    if job.remote and not row["remote"]:
        updates["remote"] = 1
    if job.extra.get("relocation") and not row["relocation"]:
        updates["relocation"] = 1

    # richer description wins the description slot
    if len(job.description or "") > len(row["description"] or ""):
        updates["description"] = job.description
        updates["needs_detail"] = 0

    # URL by source priority, not arrival order
    if job.url and URL_PRIORITY.get(job.source, 1) > URL_PRIORITY.get(row["url_source"] or "", 1):
        updates["url"] = job.url
        updates["url_source"] = job.source

    for col, val in [("salary_text", job.salary_text), ("salary_min", job.salary_min),
                     ("salary_max", job.salary_max), ("salary_currency", job.salary_currency),
                     ("contact_name", job.contact_name), ("contact_url", job.contact_url),
                     ("skills", json.dumps(job.skills) if job.skills else None),
                     ("company_li_url", job.extra.get("company_li_url")),
                     ("contact_email", job.extra.get("contact_email")),
                     ("contact_email_source", job.extra.get("contact_email_source")),
                     ("expires_at", job.extra.get("expires_at")),
                     ("location_restriction", job.extra.get("location_restriction")),
                     ("exp_min", job.extra.get("exp_min")),
                     ("exp_max", job.extra.get("exp_max"))]:
        if val and not row[col]:
            updates[col] = val

    # newer posted_at advances the row
    new_epoch = job.extra.get("posted_at_epoch")
    old_epoch = row["posted_at_epoch"]
    if new_epoch and (not old_epoch or new_epoch > old_epoch):
        updates["posted_at"] = job.posted_at
        updates["posted_at_epoch"] = new_epoch
        # fresh re-post onto a terminal row → resurface
        if (row["status"] in TERMINAL and old_epoch
                and new_epoch - old_epoch > freshness_hours * 3600):
            updates["status"] = "new"
            updates["status_updated_at"] = now
            print(f"  [db] resurfacing {row['company']} / {row['title']} "
                  f"(was {row['status']}, fresh re-post)")

    # rescore from the merged view of the row
    if rescore is not None:
        merged_desc = updates.get("description", row["description"] or "")
        merged_skills = json.loads(updates.get("skills", row["skills"] or "[]"))
        new_score, new_matched = rescore(row["title"], merged_desc, merged_skills)
        updates["score"] = new_score
        updates["matched_skills"] = json.dumps(new_matched)
    elif "description" in updates:
        updates["score"] = score
        updates["matched_skills"] = json.dumps(matched)

    sets = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*updates.values(), rid))
    conn.commit()
    return "merged"


def set_status(conn: sqlite3.Connection, jid: str, status: str, table: str = "jobs"):
    if table not in ("jobs", "companies"):
        raise ValueError(table)
    now = now_iso()
    conn.execute(f"UPDATE {table} SET status=?, status_updated_at=? WHERE id=?",
                 (status, now, jid))
    # history feeds the activity calendar — current status alone loses dates
    conn.execute("INSERT INTO status_events (kind, ref_id, status, at) VALUES (?,?,?,?)",
                 ("job" if table == "jobs" else "company", jid, status, now))
    conn.commit()


def set_field(conn: sqlite3.Connection, jid: str, field: str, value, table: str = "jobs"):
    allowed = {
        "jobs": {"notes", "contact_name", "contact_url", "contact_email",
                 "contact_email_source", "seen_at"},
        "companies": {"notes", "domain", "fit_note", "website", "li_url", "suitability"},
    }
    if table not in allowed or field not in allowed[table]:
        raise ValueError(f"{table}.{field} not editable")
    conn.execute(f"UPDATE {table} SET {field}=? WHERE id=?", (value, jid))
    conn.commit()


def mark_seen(conn: sqlite3.Connection, jid: str):
    conn.execute("UPDATE jobs SET seen_at=? WHERE id=? AND seen_at IS NULL",
                 (now_iso(), jid))
    conn.commit()


def archive_backlog(conn: sqlite3.Connection, *, junk_score: float = 20,
                    low_score: float = 40, low_days: int = 5,
                    posted_days: int = 10, unseen_days: int = 7) -> int:
    """Keep the 'new' pool honest. Archives (never deletes — deletion would
    let the same listing re-enter as new on the next fetch):
      - junk:    score < junk_score (will never surface)
      - stale-low: score < low_score and posted > low_days ago
      - expired: posted > posted_days ago, or not seen in any feed for
                 unseen_days (likely filled/delisted)
    Tracked rows (any status other than 'new') are never touched.
    """
    import time as _t
    now_epoch = int(_t.time())
    cur = conn.execute(
        """UPDATE jobs SET status='archived', status_updated_at=?
           WHERE status='new' AND (
              score < ?
              OR (score < ? AND posted_at_epoch IS NOT NULL AND posted_at_epoch < ?)
              OR (posted_at_epoch IS NOT NULL AND posted_at_epoch < ?)
              OR fetched_at < datetime('now', ?)
           )""",
        (now_iso(), junk_score, low_score,
         now_epoch - low_days * 86400, now_epoch - posted_days * 86400,
         f"-{unseen_days} days"))
    conn.commit()
    return cur.rowcount


def archive_stale(conn: sqlite3.Connection, days: int = 14) -> int:
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    cur = conn.execute(
        """UPDATE jobs SET status='archived', status_updated_at=?
           WHERE status='new' AND fetched_at IS NOT NULL
           AND CAST(strftime('%s', fetched_at) AS INTEGER) < ?""",
        (now_iso(), int(cutoff)))
    conn.commit()
    return cur.rowcount


def company_id(name: str) -> str:
    return hashlib.sha1(norm_company(name).encode()).hexdigest()[:16]


def upsert_company(conn: sqlite3.Connection, name: str, status: str = "prospect",
                   **fields) -> str:
    """status='tracked' rows exist only for tier classification and never show
    in the prospects list; a later 'prospect' upsert promotes them."""
    cid = company_id(name)
    row = conn.execute("SELECT id, status FROM companies WHERE id=?", (cid,)).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO companies (id, name, domain, li_url, website, fit_note,
               suitability, status, status_updated_at, created_at, tier)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, name, fields.get("domain", ""), fields.get("li_url", ""),
             fields.get("website", ""), fields.get("fit_note", ""),
             fields.get("suitability", ""), status, now_iso(), now_iso(),
             fields.get("tier", "")))
    else:
        for col in ("domain", "li_url", "website", "fit_note", "suitability", "tier"):
            if fields.get(col):
                conn.execute(
                    f"UPDATE companies SET {col}=? WHERE id=? AND ({col}='' OR {col} IS NULL)",
                    (fields[col], cid))
        if row["status"] == "tracked" and status == "prospect":
            conn.execute("UPDATE companies SET status='prospect' WHERE id=?", (cid,))
    conn.commit()
    return cid


def add_contact(conn: sqlite3.Connection, *, job_id: str = "", company_id: str = "",
                name: str = "", url: str = "", email: str = "", source: str = ""):
    dup = conn.execute(
        """SELECT 1 FROM contacts WHERE job_id=? AND company_id=? AND
           ((url != '' AND url=?) OR (email != '' AND email=?))""",
        (job_id, company_id, url, email)).fetchone()
    if dup:
        return
    conn.execute(
        """INSERT INTO contacts (job_id, company_id, name, url, email, source, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (job_id, company_id, name, url, email, source, now_iso()))
    conn.commit()
