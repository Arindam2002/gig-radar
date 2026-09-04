#!/usr/bin/env python3
"""Job Scout dashboard.  Run:  streamlit run dashboard.py

Multi-page layout: sidebar is navigation (Today lands first), each section owns
the full canvas, and controls live only on the page they affect.
"""
import html as html_mod
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from jobscout import db  # noqa: E402
from jobscout import outreach  # noqa: E402
from jobscout import reader  # noqa: E402
from jobscout.normalize import salary_display  # noqa: E402

st.set_page_config(page_title="Job Scout", page_icon="🎯", layout="wide")

# Calm layered-dark design: one muted indigo accent, low-saturation semantic
# colors, reduced density. Palette grounded in 2026 dark-dashboard guidance
# (layered grays over flat black; single intentional accent; muted semantics).
st.markdown("""<style>
#MainMenu, footer, header[data-testid="stHeader"] {visibility: hidden; height: 0;}
/* the reopen-sidebar button lives inside the hidden header - keep it usable */
[data-testid="stExpandSidebarButton"] {visibility: visible; position: fixed;
    top: .7rem; left: .7rem; z-index: 999; background: rgba(26,30,39,.9);
    border-radius: 9px;}
.block-container {padding-top: 1.6rem; max-width: 980px;}
div[data-testid="stVerticalBlockBorderWrapper"], div[data-testid="stLayoutWrapper"] {border-radius: 14px;}
/* quieter metrics */
div[data-testid="stMetric"] {background: rgba(139,156,247,.06); border-radius: 10px;
                             padding: .35rem .8rem;}
div[data-testid="stMetricValue"] {font-size: 1.3rem;}
div[data-testid="stMetricLabel"] {opacity: .65;}
/* cards */
.jcard-title {font-size: 1.0rem; font-weight: 600; line-height: 1.35;}
.jcard-sub {opacity: .6; font-size: .8rem; margin: 2px 0 8px 0;}
.chip {display: inline-block; padding: 1px 10px; border-radius: 999px;
       background: rgba(148,163,184,.10); font-size: .73rem; margin: 0 5px 4px 0;
       color: rgba(215,220,229,.85);}
.chip.pay   {background: rgba(94,212,190,.10); color: #8fd6c6;}
.chip.skill {background: rgba(139,156,247,.11); color: #aab8f5;}
.chip.warn  {background: rgba(210,180,120,.10); color: #cdb98a;}
.score {font-size: 1.2rem; font-weight: 700; text-align: center;
        border-radius: 12px; padding: .5rem 0; margin-top: .15rem;}
.score.hi  {background: rgba(94,212,190,.10); color: #6fcab8;}
.score.mid {background: rgba(210,180,120,.09); color: #c2ad83;}
.score.lo  {background: rgba(148,163,184,.10); color: rgba(215,220,229,.55);}
/* controls */
.stButton button, div[data-testid="stLinkButton"] a {border-radius: 9px; width: 100%;
        padding: .16rem .55rem; font-size: .8rem; min-height: 1.9rem;
        border-color: rgba(148,163,184,.25);}
div[data-testid="stExpander"] {border-radius: 12px; border: 1px solid rgba(148,163,184,.16);
                               margin-bottom: .4rem;}
/* page headers */
.page-h {font-size: 1.25rem; font-weight: 650; margin-bottom: .2rem;}
.page-sub {opacity: .55; font-size: .84rem; margin-bottom: 1.1rem;}
/* rendered markdown (briefs, study files): tame the heading scale */
div[data-testid="stMarkdownContainer"] h1 {font-size: 1.4rem; padding-top: .4rem;}
div[data-testid="stMarkdownContainer"] h2 {font-size: 1.12rem;}
div[data-testid="stMarkdownContainer"] h3 {font-size: 1.0rem;}
div[data-testid="stMarkdownContainer"] a {color: #aab8f5;}
/* activity calendar: clickable button grid, hardened cross-browser.
   min-width:0 matters - Safari's default button min-width is what turned the
   grid into scattered pills. */
.cal-head {text-align:center; font-weight:600; font-size:.85rem; padding-top:.15rem;}
.cal-dow {font-size:.6rem; opacity:.45; text-align:center; padding:1px 0;}
.st-key-calgrid {max-width:330px; margin-left:auto;}
.st-key-calgrid div[data-testid="stHorizontalBlock"] {gap:3px !important;
    margin-bottom:3px !important;}
.st-key-calgrid div[data-testid="stColumn"] {min-width:0 !important;
    padding:0 !important; flex:1 1 0 !important;}
.st-key-calgrid div[data-testid="stElementContainer"] {width:100% !important;}
.st-key-calgrid button {width:100% !important; min-width:0 !important;
    height:30px !important; min-height:30px !important; padding:0 !important;
    font-size:.7rem !important; border-radius:7px !important; border:none !important;
    -webkit-appearance:none; appearance:none;
    background:rgba(148,163,184,.07) !important; line-height:30px !important;}
.st-key-calgrid button[data-testid="stBaseButton-primary"] {
    background:rgba(139,156,247,.32) !important; font-weight:700 !important;}
.day-ev {font-size:.85rem; margin:.15rem 0; line-height:1.45;}
.trow {font-size:.88rem; line-height:1.4;}
/* topic page: notes list */
.jsr-quote {font-size: .88rem; opacity: .75; font-style: italic; line-height: 1.4;}
.jsr-note {font-size: .92rem; margin-top: .25rem; white-space: pre-wrap;}
.jsr-note.muted {opacity: .45; font-style: italic;}
.topic-open {font-size: .82rem; white-space: nowrap;}
/* sidebar: roomier nav */
section[data-testid="stSidebar"] {min-width: 240px; max-width: 240px;}
div[data-testid="stSidebarNav"] a {border-radius: 9px;}
</style>""", unsafe_allow_html=True)

DB_PATH = os.environ.get("JOBSCOUT_DB", "")  # E2E tests point this at a seeded DB
# lock lives beside the DB so test instances never see the real dashboard's lock
from jobscout.settings import logs_dir as _logs_dir  # noqa: E402

LOCK = Path(DB_PATH).with_suffix(".lock") if DB_PATH else _logs_dir() / "refresh.lock"


def get_conn():
    return db.connect(DB_PATH or None)


# ── background refresh ──────────────────────────────────────────────

def refresh_running() -> bool:
    if not LOCK.exists():
        return False
    try:
        pid = int(LOCK.read_text().strip())
    except ValueError:
        LOCK.unlink(missing_ok=True)
        return False
    # reap our own finished child - a zombie still answers os.kill(pid, 0),
    # which is exactly how the status once got stuck on "fetching…"
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            LOCK.unlink(missing_ok=True)
            return False
    except ChildProcessError:
        pass  # not our child (e.g. dashboard restarted) - fall through
    try:
        stat = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return False
    alive = bool(stat) and not stat.startswith("Z")
    # a run takes minutes; a lock older than 30 min is stale no matter what
    if alive and (LOCK.stat().st_mtime > time.time() - 1800):
        return True
    LOCK.unlink(missing_ok=True)
    return False


def start_refresh() -> bool:
    if refresh_running() or os.environ.get("JOBSCOUT_DISABLE_REFRESH"):
        return False
    _logs_dir().mkdir(parents=True, exist_ok=True)
    log = open(_logs_dir() / "refresh.log", "a")
    log.write(f"\n── dashboard-triggered refresh {datetime.now():%Y-%m-%d %H:%M} ──\n")
    proc = subprocess.Popen([sys.executable, str(ROOT / "run.py")], cwd=ROOT,
                            stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    LOCK.write_text(str(proc.pid))
    return True


def last_updated_epoch(conn) -> float | None:
    row = conn.execute("SELECT MAX(fetched_at) m FROM jobs").fetchone()
    if not row or not row["m"]:
        return None
    try:
        return datetime.fromisoformat(row["m"]).timestamp()
    except ValueError:
        return None


from jobscout.settings import (briefs_dir, load_configs as _load_configs,  # noqa: E402
                               logs_dir, study_dir)


@st.cache_data
def load_configs():
    return _load_configs()


CFG, PROFILE = load_configs()
if os.environ.get("JOBSCOUT_NO_LLM"):  # E2E: deterministic template drafts
    CFG = {**CFG, "keys": {**CFG["keys"], "gemini_api_key": ""}}
conn = get_conn()

TIER_MAP = {r["id"]: (r["tier"] or "unknown")
            for r in conn.execute("SELECT id, tier FROM companies")}
TIER_CHIP = {"enterprise": ("🏛 enterprise", "pay"), "growth": ("🚀 growth", "skill"),
             "startup": ("🌱 startup", ""), "staffing": ("🏢 staffing", "warn")}


def tier_of(company: str) -> str:
    return TIER_MAP.get(db.company_id(company or ""), "unknown")

# auto-refresh when stale (once per browser session)
_stale_after = CFG.get("refresh", {}).get("stale_after_hours", 3) * 3600
_last = last_updated_epoch(conn)
_is_stale = _last is None or (datetime.now(timezone.utc).timestamp() - _last) > _stale_after
if "auto_refresh_done" not in st.session_state:
    st.session_state.auto_refresh_done = True
    if CFG.get("refresh", {}).get("auto_on_open", True) and _is_stale and start_refresh():
        st.toast("Data was stale - fetching fresh listings in the background…", icon="⟳")


# ── small helpers ───────────────────────────────────────────────────

def page_header(title: str, sub: str = ""):
    st.markdown(f'<div class="page-h">{title}</div>'
                + (f'<div class="page-sub">{sub}</div>' if sub else ""),
                unsafe_allow_html=True)


def age_str(epoch) -> str:
    if not epoch or pd.isna(epoch):
        return ""
    h = (datetime.now(timezone.utc) - datetime.fromtimestamp(epoch, tz=timezone.utc)
         ).total_seconds() / 3600
    if h < 1:
        return "just now"
    if h < 24:
        return f"{int(h)}h ago"
    return f"{int(h // 24)}d ago"


def salary_of(row) -> str:
    return salary_display(row["salary_min"], row["salary_max"],
                          row["salary_currency"], row["salary_text"])


def esc(s) -> str:
    return html_mod.escape(str(s or ""))


def score_class(s: float) -> str:
    return "hi" if s >= 70 else ("mid" if s >= 45 else "lo")


def card_html(row) -> str:
    matched = json.loads(row["matched_skills"] or "[]")
    chips = []
    sal = salary_of(row)
    if sal != "-":
        chips.append(f'<span class="chip pay">💰 {esc(sal)}</span>')
    if row["exp_min"] is not None and not pd.isna(row["exp_min"]):
        hi = row["exp_max"]
        rng = f"{int(row['exp_min'])}–{int(hi)}" if hi and not pd.isna(hi) else f"{int(row['exp_min'])}+"
        chips.append(f'<span class="chip">{rng} yrs</span>')
    if row.get("seniority") and row["seniority"] not in ("", "unclear"):
        chips.append(f'<span class="chip">{esc(row["seniority"])}</span>')
    tier = tier_of(row["company"])
    if tier in TIER_CHIP:
        label, cls = TIER_CHIP[tier]
        chips.append(f'<span class="chip {cls}">{label}</span>')
    if row.get("abroad"):
        where = row.get("location_restriction") or row.get("location") or "abroad"
        chips.append(f'<span class="chip warn">🌍 {esc(where)}</span>')
    if row.get("relocation"):
        chips.append('<span class="chip pay">✈️ visa/relocation mentioned</span>')
    for m in matched[:5]:
        chips.append(f'<span class="chip skill">{esc(m)}</span>')
    if row["needs_detail"]:
        chips.append('<span class="chip warn">no JD yet</span>')

    srcs = ", ".join(json.loads(row["sources"] or "[]")) or row["source"]
    sub_bits = [b for b in (esc(row["company"]), esc(row["location"]),
                            age_str(row["posted_at_epoch"]), f"via {esc(srcs)}") if b]
    return (f'<div class="jcard-title">{esc(row["title"])}</div>'
            f'<div class="jcard-sub">{" · ".join(sub_bits)}</div>'
            f'<div>{"".join(chips)}</div>')


def job_card(row, conn):
    with st.container(border=True):
        c_score, c_body, c_act = st.columns([0.62, 4.7, 1.25])
        c_score.markdown(
            f'<div class="score {score_class(row["score"])}">{row["score"]:.0f}</div>',
            unsafe_allow_html=True)
        c_body.markdown(card_html(row), unsafe_allow_html=True)
        with c_act:
            if row["url"]:
                st.link_button("Apply ↗", row["url"])
            if st.button("⭐ Shortlist", key=f"sl_{row['id']}"):
                db.set_status(conn, row["id"], "shortlisted")
                st.toast(f"Shortlisted {row['company']}", icon="⭐")
                st.rerun()
            if st.button("✕ Dismiss", key=f"dm_{row['id']}"):
                db.set_status(conn, row["id"], "dismissed")
                st.rerun()
        if row["description"] or row["contact_email"]:
            with st.expander("Description & contact"):
                if row["contact_email"]:
                    st.info(f"📧 Contact found in posting: **{row['contact_email']}**")
                st.markdown(esc(row["description"][:2000]) +
                            ("…" if len(row["description"] or "") > 2000 else ""))
    db.mark_seen(conn, row["id"])


def jobs_df(where: str = "1=1", params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(
        f"SELECT * FROM jobs WHERE {where}", conn, params=params)


# ── study topic links ───────────────────────────────────────────────
# STUDY.md, the briefs and the topic files link topics as plain file paths
# (readable anywhere): "topics/<slug>.md" from STUDY.md, "../study/topics/
# <slug>.md" from a brief, and bare "<slug>.md" between sibling topics. At
# render time every shape becomes a study?topic=<slug> link to the topic page.
# Links to .md files that are not topics (session-notes.md) are left alone.
_TOPIC_MD_LINK = re.compile(
    r"\[([^\]]+)\]\((?:\.\./)?(?:study/)?(?:topics/)?(?:\./)?"
    r"([a-zA-Z0-9\-_]+)\.md(?:#[^)]*)?\)")


def linkify_topics(md: str) -> str:
    topics = study_dir() / "topics"

    def sub(m):
        if not (topics / f"{m.group(2)}.md").exists():
            return m.group(0)
        return f'<a href="study?topic={m.group(2)}" target="_self">📖 {m.group(1)}</a>'
    return _TOPIC_MD_LINK.sub(sub, md)


def _studied_at(completed_at: str | None, f: Path):
    """When the topic was ticked off, or None if never / if the file was
    deepened after that (a topic rewritten since you read it is due again)."""
    if not completed_at:
        return None
    try:
        dt = datetime.fromisoformat(completed_at)
    except ValueError:
        return None
    return dt if dt.timestamp() >= f.stat().st_mtime else None


def _back_to_study():
    st.markdown('<a href="study" target="_self">← Back to Study</a>',
                unsafe_allow_html=True)


def topic_article() -> bool:
    """?topic=<slug> turns any page into that topic's reading page: just the
    article, your highlights and notes, and the mark-as-studied control.
    Returns True when it rendered (the caller then skips its own page)."""
    tp = st.query_params.get("topic")
    if not tp:
        return False
    slug = re.sub(r"[^a-zA-Z0-9_-]", "", tp)
    f = study_dir() / "topics" / f"{slug}.md"
    if not f.exists():
        _back_to_study()
        st.warning(f"There is no study topic called `{slug}`.")
        return True

    key = f"reader_{slug}"

    def on_action():
        # Runs before the script body on every highlight action from the page,
        # possibly on a different thread than the run that created the global
        # connection (sqlite objects are thread-bound), so open a private one.
        act = getattr(st.session_state.get(key), "action", None)
        if not isinstance(act, dict):
            return
        kind = act.get("type")
        c = get_conn()
        try:
            if kind == "add":
                db.add_study_note(c, slug, act.get("quote", ""), act.get("prefix", ""),
                                  act.get("suffix", ""), act.get("note", ""))
            elif kind == "update" and act.get("id") is not None:
                db.update_study_note(c, act["id"], act.get("note", ""))
            elif kind == "delete" and act.get("id") is not None:
                db.delete_study_note(c, act["id"])
        except ValueError:
            pass
        finally:
            c.close()

    completed_at = db.study_progress_map(conn).get(slug)
    studied_on = _studied_at(completed_at, f)
    notes = db.study_notes(conn, slug)

    top1, top2 = st.columns([2, 3], vertical_alignment="center")
    with top1:
        _back_to_study()
    if studied_on:
        status = f"✓ studied on {studied_on.astimezone().strftime('%d %b %Y')}"
    elif completed_at:
        status = "updated since you studied it"
    else:
        status = "not studied yet"
    top2.markdown(f'<div class="page-sub" style="text-align:right; margin:0">'
                  f'{status} · {len(notes)} note{"s" if len(notes) != 1 else ""}</div>',
                  unsafe_allow_html=True)

    focus = st.session_state.pop("_reader_focus", None)
    reader.topic_reader(reader.topic_html(linkify_topics(f.read_text())), notes,
                        key=key, on_action=on_action, focus_id=focus)

    # ── notes ──
    st.markdown("#### 🖍 Your highlights and notes")
    if not notes:
        st.caption("Select any text in the article to highlight it, with or without "
                   "a note. Everything you mark is listed here so you can come back "
                   "to it; click a highlight in the text to edit or remove it.")
    for n in notes:
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([8, 1, 1, 1], vertical_alignment="center")
            body = f'<div class="jsr-quote">“{reader.quote_preview(n["quote"])}”</div>'
            body += (f'<div class="jsr-note">{esc(n["note"])}</div>' if n["note"]
                     else '<div class="jsr-note muted">no note</div>')
            c1.markdown(body, unsafe_allow_html=True)
            if c2.button(":material/my_location:", key=f"note_go_{n['id']}",
                         help="Show in the text"):
                st.session_state["_reader_focus"] = n["id"]
                st.rerun()
            with c3.popover(":material/edit:", help="Edit the note"):
                new = st.text_area("Note", value=n["note"] or "",
                                   key=f"note_edit_{n['id']}", label_visibility="collapsed")
                if st.button("Save", key=f"note_save_{n['id']}", type="primary"):
                    db.update_study_note(conn, n["id"], new.strip())
                    st.rerun()
            if c4.button(":material/delete:", key=f"note_del_{n['id']}",
                         help="Remove this highlight"):
                db.delete_study_note(conn, n["id"])
                st.rerun()

    # ── done with it? ──
    st.divider()
    b1, b2 = st.columns([1.4, 3], vertical_alignment="center")
    if studied_on:
        if b1.button("Mark as not studied", key=f"unstudy_{slug}"):
            db.set_study_done(conn, slug, False)
            st.rerun()
        b2.caption("Ticked off. Unticking puts it back in today's session and "
                   "pauses the routine's deepening of it.")
    else:
        label = "✓ Mark as studied again" if completed_at else "✓ Mark as studied"
        if b1.button(label, key=f"study_{slug}", type="primary"):
            db.set_study_done(conn, slug, True)
            st.toast("Marked as studied", icon="✅")
            st.rerun()
        b2.caption("Once ticked, the daily routine may deepen this topic and its "
                   "spaced-repetition cycle (1, 3, 7, 21 days) starts.")
    return True


# ── pages ───────────────────────────────────────────────────────────

BRIEFS_DIR = briefs_dir()

# a "pick" line: a bullet ("- **87** [job](url)") OR a numbered pick in any
# bold variant ("**1. [job](url)** ·", "1. **[job](url)**") - agent-written
# briefs drift in format, so match the shapes, not one shape. Table rows and
# blockquotes are left alone (splitting them out would mangle the markdown).
_BULLET_LINK = re.compile(
    r"^\s*(?:[-*]\s+|\*{0,2}\d+\.\s*\*{0,2})"
    r"[^|>]*?\[(?P<title>[^\]]+)\]\((?P<url>https?://[^\)]+)\)"
    r".*?(?:\s+[—–-]\s+(?P<company>[^·—–-]+))?$")


def _job_for_bullet(m) -> "sqlite3.Row | None":
    # primary: URL hash. Fallback: title (+ company) - the stored URL may have
    # been upgraded to another source's link by the dedupe merge.
    uh = db.url_hash(m.group("url"))
    if uh:
        row = conn.execute("SELECT id, status, company FROM jobs WHERE url_hash=?",
                           (uh,)).fetchone()
        if row:
            return row
    # link text is either "Title" or "Title — Company, City" - try both splits
    link_text = m.group("title").strip()
    candidates = [(link_text, (m.group("company") or "").strip())]
    parts = re.split(r"\s+[—–]\s+| - ", link_text, maxsplit=1)
    if len(parts) == 2:
        comp_part = parts[1].split(",")[0].strip()  # "Mastercard, Pune" -> "Mastercard"
        candidates.insert(0, (parts[0].strip(), comp_part))
    for title, company in candidates:
        rows = conn.execute("SELECT id, status, company FROM jobs WHERE title=?",
                            (title,)).fetchall()
        if len(rows) == 1:
            return rows[0]
        comp = db.norm_company(company)
        if comp:
            for r in rows:
                rc = db.norm_company(r["company"])
                if rc.startswith(comp) or comp.startswith(rc):
                    return r
    return None


_ANY_MD_LINK = re.compile(r"\[[^\]]*\]\((https?://[^\)]+)\)")


def _load_picks_manifest() -> dict:
    """briefs/TODAY.picks.json: [{url, title, company}] written by the routine.
    The durable contract - prose formats drift, the manifest does not."""
    f = BRIEFS_DIR / "TODAY.picks.json"
    if not f.exists():
        return {}
    try:
        picks = json.loads(f.read_text())
        return {p["url"]: p for p in picks if isinstance(p, dict) and p.get("url")}
    except (json.JSONDecodeError, TypeError):
        return {}


def _job_for_line(line: str, manifest: dict):
    """Resolve a brief line to a DB job. Three layers, most reliable first:
    1. any URL on the line that is in the manifest (resolved via url/title),
    2. any URL on the line whose hash is in the DB,
    3. the shaped bullet/numbered-pick matcher with title+company fallback.
    Tables, blockquotes and headings are never touched."""
    stripped = line.lstrip()
    if not stripped or stripped[0] in "|>#":
        return None
    urls = _ANY_MD_LINK.findall(line)
    for url in urls:
        uh = db.url_hash(url)
        if uh:
            row = conn.execute("SELECT id, status, company FROM jobs WHERE url_hash=?",
                               (uh,)).fetchone()
            if row:
                return row
        p = manifest.get(url)
        if p:
            rows = conn.execute("SELECT id, status, company FROM jobs WHERE title=?",
                                (p.get("title", ""),)).fetchall()
            comp = db.norm_company(p.get("company", ""))
            for r in rows:
                rc = db.norm_company(r["company"])
                if len(rows) == 1 or rc.startswith(comp) or (comp and comp.startswith(rc)):
                    return r
    m = _BULLET_LINK.match(line)
    return _job_for_bullet(m) if m else None


def render_brief_interactive(md: str):
    """Render the brief, attaching status actions to every pick that maps to a
    DB row - apply from the brief itself instead of re-finding the job."""
    manifest = _load_picks_manifest()
    buffer: list[str] = []

    def flush():
        if buffer:
            st.markdown(linkify_topics("\n".join(buffer)), unsafe_allow_html=True)
            buffer.clear()

    for line in md.splitlines():
        job = _job_for_line(line, manifest)
        if job is None:
            buffer.append(line)
            continue
        flush()
        c_text, c_act = st.columns([5.1, 1.5], vertical_alignment="center")
        c_text.markdown(line, unsafe_allow_html=True)
        with c_act:
            if job["status"] == "new":
                b1, b2, b3 = st.columns(3)
                if b1.button("⭐", key=f"td_sl_{job['id']}", help="Shortlist"):
                    db.set_status(conn, job["id"], "shortlisted")
                    st.toast(f"Shortlisted {job['company']}", icon="⭐")
                    st.rerun()
                if b2.button("✓", key=f"td_ap_{job['id']}", help="Mark applied"):
                    db.set_status(conn, job["id"], "applied")
                    st.toast(f"Applied - {job['company']}", icon="✅")
                    st.rerun()
                if b3.button("✕", key=f"td_dm_{job['id']}", help="Dismiss"):
                    db.set_status(conn, job["id"], "dismissed")
                    st.rerun()
            else:
                st.markdown(f'<span class="chip pay">✓ {esc(job["status"])}</span>',
                            unsafe_allow_html=True)
    flush()


def page_today():
    if topic_article():
        return
    today_md = BRIEFS_DIR / "TODAY.md"
    if today_md.exists():
        mtime = datetime.fromtimestamp(today_md.stat().st_mtime, tz=timezone.utc)
        age_h = (datetime.now(timezone.utc) - mtime).total_seconds() / 3600
        if age_h > 26:
            st.warning(f"This brief is {age_h/24:.1f} days old - run "
                       "`python brief.py` (or wait for the daily routine).")
        render_brief_interactive(today_md.read_text())
    else:
        page_header("📋 Today")
        st.info("No brief yet. Generate your first daily brief:")
        st.code("cd " + str(ROOT) + " && .venv/bin/python brief.py", language="bash")
        st.caption("It refreshes listings, picks a bounded ~10-job apply sprint "
                   "(balanced across AI-infra / backend-.NET / abroad, decent "
                   "companies preferred), lists follow-ups due, and generates "
                   "today's interview-prep drop + study topics.")


def page_fresh():
    if topic_article():
        return
    page_header("🔥 Fresh matches", "score · pick · apply - filters live in the sidebar")

    # filters belong to THIS page only
    with st.sidebar:
        st.markdown("**Filters**")
        min_score = st.slider("Min match score", 0, 100, 40, key="f_score")
        freshness_days = st.slider("Posted within (days)", 1, 30, 7, key="f_fresh")
        max_exp = st.slider("Max years required", 0, 10, 4, key="f_exp",
                            help="Hide jobs demanding more experience than this. "
                                 "Jobs that don't state a requirement stay visible.")
        with st.expander("Salary"):
            include_undisclosed = st.checkbox("Include undisclosed", True, key="f_undisc")
            min_lpa = st.slider("Min ₹ LPA (India)", 0, 80,
                                CFG["salary"].get("india_floor_lpa", 28), key="f_lpa")
            min_usd = st.slider("Min $K/yr (remote)", 0, 300,
                                CFG["salary"].get("remote_floor_usd", 60000) // 1000,
                                key="f_usd")
        with st.expander("Company & sources"):
            tier_filter = st.multiselect(
                "Company tier", ["enterprise", "growth", "startup", "staffing", "unknown"],
                default=[], key="f_tier", help="Empty = all")
            sources = st.multiselect(
                "Sources", ["linkedin", "naukri", "himalayas", "remotive", "remoteok",
                            "arbeitnow", "adzuna", "jsearch"], default=[], key="f_sources",
                help="Empty = all")
            unseen_only = st.checkbox("Only jobs I haven't seen", False, key="f_unseen")

    f1, f2, f3 = st.columns([2.1, 1.5, 2.0])
    market = f1.segmented_control("Market", ["All", "India", "Remote", "Abroad"],
                                  default="All", key="f_market") or "All"
    sort_by = f2.segmented_control("Sort", ["Best match", "Newest"],
                                   default="Best match", key="f_sort") or "Best match"
    text_q = f3.text_input("Search", key="f_text", placeholder="title, company, keyword…",
                           label_visibility="collapsed")

    def apply_filters(df):
        if df.empty:
            return df
        out = df[df["score"] >= min_score]
        if market == "India":
            out = out[(out["remote"] == 0) & (out["abroad"] == 0)]
        elif market == "Remote":
            out = out[(out["remote"] == 1) & (out["abroad"] == 0)]
        elif market == "Abroad":
            out = out[out["abroad"] == 1]
        if sources:
            out = out[out["source"].isin(sources)]
        if tier_filter:
            out = out[out["company"].map(tier_of).isin(tier_filter)]
        cutoff = (datetime.now(timezone.utc) - timedelta(days=freshness_days)).timestamp()
        out = out[(out["posted_at_epoch"].fillna(0) >= cutoff) | out["posted_at_epoch"].isna()]
        if unseen_only:
            out = out[out["seen_at"].isna()]
        out = out[out["exp_min"].isna() | (out["exp_min"] <= max_exp)]
        if out.empty:
            # .apply() on an empty frame returns a column-less result that
            # breaks boolean indexing and the later sort - bail out intact
            return out

        def sal_ok(r):
            if r["salary_min"] is None or pd.isna(r["salary_min"]):
                return include_undisclosed
            if r["salary_currency"] == "INR":
                return r["salary_max"] >= min_lpa
            if str(r["salary_currency"]).startswith("USD"):
                return r["salary_max"] >= min_usd * 1000
            return include_undisclosed
        out = out[out.apply(sal_ok, axis=1)]
        if text_q and not out.empty:
            q = text_q.lower()
            out = out[out.apply(
                lambda r: q in f"{r['title']} {r['company']} {r['description']}".lower(),
                axis=1)]
        return out

    df = apply_filters(jobs_df("status='new'"))
    if sort_by == "Newest":
        df = df.sort_values(["posted_at_epoch", "score"], ascending=False, na_position="last")
    elif market == "Abroad":
        # visa/relocation-friendly roles are the whole point of this view
        df = df.sort_values(["relocation", "score", "posted_at_epoch"],
                            ascending=False, na_position="last")
    else:
        df = df.sort_values(["score", "posted_at_epoch"], ascending=False, na_position="last")

    show_n = st.session_state.get("fresh_show_n", 12)
    st.caption(f"{len(df)} matching jobs · showing {min(len(df), show_n)}")
    if df.empty:
        st.info("No matches with the current filters. Hit **⟳ Refresh data** in the "
                "sidebar for fresh listings, or loosen the filters.")
    for _, row in df.head(show_n).iterrows():
        job_card(row, conn)
    if len(df) > show_n:
        _, mid, _ = st.columns([2, 1.4, 2])
        if mid.button(f"Show {min(12, len(df) - show_n)} more", key="show_more"):
            st.session_state.fresh_show_n = show_n + 12
            st.rerun()


def _contact_lines(kind: str, row_id: str) -> pd.DataFrame:
    return pd.read_sql_query(
        f"SELECT name, url, email, source FROM contacts WHERE {kind}=?",
        conn, params=(row_id,))


def _render_contacts(df: pd.DataFrame):
    if df.empty:
        st.caption("No contacts yet.")
        return
    for _, ct in df.iterrows():
        line = " · ".join(x for x in (ct["name"], ct["email"]) if x)
        if ct["url"]:
            line += f" · [profile ↗]({ct['url']})"
        st.markdown(f"- {line} <span class='chip'>{esc(ct['source'])}</span>",
                    unsafe_allow_html=True)


def _outreach_job(row):
    with st.expander(f"{row['title']} - {row['company']}  ·  {row['status'].upper()}"):
        suit = outreach.cold_mail_suitability(row["company"], CFG["companies"].get("boost"))
        st.caption("🎯 good cold-mail target" if suit == "good"
                   else "🏢 big company - apply via their portal, mail as a nudge")

        # one action row: links · contact tools · status
        a1, a2, a3, a4 = st.columns([1.1, 1.4, 1.3, 1.3])
        with a1:
            if row["url"]:
                st.link_button("Listing ↗", row["url"])
        with a2:
            if row["company_li_url"]:
                st.link_button("Hiring team ↗",
                               row["company_li_url"].rstrip("/") + "/people/")
        with a3:
            with st.popover("👤 Contact"):
                name = st.text_input("Name", row["contact_name"], key=f"cn_{row['id']}")
                email = st.text_input("Email", row["contact_email"], key=f"ce_{row['id']}")
                curl = st.text_input("LinkedIn", row["contact_url"], key=f"cu_{row['id']}")
                if st.button("💾 Save", key=f"sv_{row['id']}"):
                    db.set_field(conn, row["id"], "contact_name", name)
                    db.set_field(conn, row["id"], "contact_email", email)
                    db.set_field(conn, row["id"], "contact_url", curl)
                    st.toast("Saved", icon="💾")
                if st.button("🔎 Find HR contacts", key=f"hr_{row['id']}",
                             help="Firecrawl web search for public recruiter/TA profiles"):
                    with st.spinner("Searching public profiles…"):
                        try:
                            found = outreach.find_hr_profiles(row["company"])
                            for p in found:
                                db.add_contact(conn, job_id=row["id"], name=p["name"],
                                               url=p["url"], source="firecrawl search")
                            st.toast(f"{len(found)} profiles found" if found else "None found")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Search failed: {e}")
                hunter_key = CFG["keys"].get("hunter_api_key")
                if hunter_key:
                    domain = st.text_input("Domain", outreach.guess_domain(row["company"]),
                                           key=f"dom_{row['id']}")
                    if st.button("Hunter: email pattern", key=f"hu_{row['id']}"):
                        try:
                            res = outreach.hunter_domain_search(domain, hunter_key)
                            st.write(f"Pattern: `{res['pattern']}`")
                            for e in res["emails"]:
                                st.write(f"- {e['email']} ({e['name']} - {e['position']})")
                        except Exception as e:
                            st.error(f"Hunter failed: {e}")
        with a4:
            if row["status"] == "shortlisted" and st.button("✓ Mark applied",
                                                            key=f"ap2_{row['id']}"):
                db.set_status(conn, row["id"], "applied")
                st.rerun()
            if row["status"] != "outreach" and st.button("✉️ Outreach sent",
                                                         key=f"os_{row['id']}"):
                db.set_status(conn, row["id"], "outreach")
                st.rerun()

        _render_contacts(_contact_lines("job_id", row["id"]))
        if row["contact_email"] and _contact_lines("job_id", row["id"]).empty:
            st.markdown(f"- {row['contact_email']} "
                        f"<span class='chip'>{esc(row['contact_email_source'] or 'saved')}</span>",
                        unsafe_allow_html=True)

        gem_key = CFG["keys"].get("gemini_api_key")
        if st.button("📝 Draft email" + (" (Gemini)" if gem_key else ""),
                     key=f"dr_{row['id']}"):
            payload = {
                "title": row["title"], "company": row["company"],
                "matched_skills": json.loads(row["matched_skills"] or "[]"),
                "description": row["description"] or ""}
            d = None
            if gem_key:
                try:
                    with st.spinner("Drafting with Gemini…"):
                        d = outreach.draft_email_gemini(
                            PROFILE, payload, gem_key,
                            model=CFG.get("llm", {}).get("draft_model",
                                                         "gemini-3.6-flash"))
                except Exception as e:
                    st.warning(f"Gemini failed ({e}) - using template draft")
            if d is None:
                d = outreach.draft_email(PROFILE, payload)
            greet = f" {row['contact_name'].split()[0]}" if row["contact_name"] else ""
            st.text_input("Subject", d["subject"], key=f"sub_{row['id']}")
            st.text_area("Body - copy & send from your mail",
                         d["body"].replace("{name}", greet), height=280,
                         key=f"bod_{row['id']}")


def _outreach_prospect(comp):
    with st.expander(f"{comp['name']}  ·  {comp['status'].upper()}"):
        if comp["fit_note"]:
            st.caption(comp["fit_note"])
        p1, p2, p3, p4 = st.columns([1.2, 1.4, 1.4, 1.4])
        with p1:
            if comp["li_url"]:
                st.link_button("Hiring team ↗", comp["li_url"].rstrip("/") + "/people/")
        if p2.button("🔎 Find HR contacts", key=f"chr_{comp['id']}"):
            with st.spinner("Searching…"):
                try:
                    found = outreach.find_hr_profiles(comp["name"])
                    for p in found:
                        db.add_contact(conn, company_id=comp["id"], name=p["name"],
                                       url=p["url"], source="firecrawl search")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
        draft = p3.button("📝 Speculative draft", key=f"cdr_{comp['id']}")
        with p4:
            new_status = st.selectbox("Status", db.COMPANY_STATUSES,
                                      index=db.COMPANY_STATUSES.index(comp["status"]),
                                      key=f"cst_{comp['id']}", label_visibility="collapsed")
            if new_status != comp["status"]:
                db.set_status(conn, comp["id"], new_status, table="companies")
                st.rerun()
        _render_contacts(_contact_lines("company_id", comp["id"]))
        if draft:
            d = outreach.draft_speculative(PROFILE, comp["name"], comp["fit_note"])
            st.text_input("Subject", d["subject"], key=f"csub_{comp['id']}")
            st.text_area("Body", d["body"].replace("{name}", ""), height=260,
                         key=f"cbod_{comp['id']}")


def page_outreach():
    if topic_article():
        return
    page_header("✉️ Outreach", "find a contact, draft the mail, send from your own "
                "inbox, mark it sent")
    view = st.segmented_control(
        "View", ["Jobs", "Prospects"], default="Jobs", key="o_view",
        label_visibility="collapsed") or "Jobs"

    if view == "Jobs":
        odf = jobs_df("status IN ('shortlisted','applied','outreach','replied')")
        odf = odf.sort_values("status_updated_at", ascending=False)
        if odf.empty:
            st.info("Nothing here yet - hit **⭐ Shortlist** on a job in Fresh matches.")
        for _, row in odf.iterrows():
            _outreach_job(row)
    else:
        st.caption("Companies worth a speculative 'any openings?' mail - even without "
                   "a listing. Grows automatically; add more with `python run.py --prospects`.")
        cdf = pd.read_sql_query(
            """SELECT * FROM companies
               WHERE status IN ('prospect','outreach','replied','conversation')
               ORDER BY status_updated_at DESC""", conn)
        if cdf.empty:
            st.info("No prospects yet - run `python run.py --prospects`.")
        for _, comp in cdf.head(30).iterrows():
            _outreach_prospect(comp)


def _activity_calendar():
    """Day details on the left, a compact clickable month grid on the right.

    The grid uses real Streamlit buttons + session state - NOT ?day= links:
    anchor navigation forces a full browser reload, buttons rerun in place.
    """
    import calendar as cal_mod

    today = datetime.now().date()

    counts = {r["d"]: r["n"] for r in conn.execute(
        """SELECT date(at) d, COUNT(*) n FROM status_events
           WHERE kind='job' AND status IN ('applied','outreach')
           GROUP BY date(at)""")}

    left, right = st.columns([1.5, 1], gap="large")

    if "cal_month" not in st.session_state:
        st.session_state.cal_month = (today.year, today.month)
    if "cal_day" not in st.session_state:
        st.session_state.cal_day = today.isoformat()
    year, month = st.session_state.cal_month
    sel_day = st.session_state.cal_day

    with right, st.container(key="calgrid"):
        n1, n2, n3 = st.columns([1, 3.2, 1])
        if n1.button("‹", key="cal_prev"):
            d = datetime(year, month, 1) - timedelta(days=1)
            st.session_state.cal_month = (d.year, d.month)
            st.rerun()
        n2.markdown(f'<div class="cal-head">{cal_mod.month_name[month]} {year}</div>',
                    unsafe_allow_html=True)
        if n3.button("›", key="cal_next"):
            d = datetime(year, month, 28) + timedelta(days=5)
            st.session_state.cal_month = (d.year, d.month)
            st.rerun()
        dows = st.columns(7)
        for i, d in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            dows[i].markdown(f'<div class="cal-dow">{d}</div>', unsafe_allow_html=True)
        for week in cal_mod.Calendar().monthdatescalendar(year, month):
            cols = st.columns(7)
            for i, day in enumerate(week):
                if day.month != month:
                    continue
                iso = day.isoformat()
                n = counts.get(iso, 0)
                label = f"{day.day}·{n}" if n else str(day.day)
                if cols[i].button(label, key=f"cd_{iso}",
                                  type="primary" if iso == sel_day else "secondary"):
                    st.session_state.cal_day = iso
                    st.rerun()

    with left:
        day_ev = pd.read_sql_query(
            """SELECT e.status, j.title, j.company, j.url FROM status_events e
               JOIN jobs j ON j.id = e.ref_id
               WHERE e.kind='job' AND date(e.at) = ? ORDER BY e.at""",
            conn, params=(sel_day,))
        comp_ev = pd.read_sql_query(
            """SELECT e.status, c.name FROM status_events e
               JOIN companies c ON c.id = e.ref_id
               WHERE e.kind='company' AND date(e.at) = ? ORDER BY e.at""",
            conn, params=(sel_day,))
        nice = datetime.fromisoformat(sel_day).strftime("%a, %d %b")
        if day_ev.empty and comp_ev.empty:
            st.caption(f"{nice} - no activity. Pick a highlighted day on the calendar.")
            return
        applied_n = (day_ev["status"] == "applied").sum()
        out_n = (day_ev["status"] == "outreach").sum() + \
                (comp_ev["status"] == "outreach").sum()
        st.markdown(f"**{nice}** · {applied_n} applied · {out_n} outreach")
        icons = {"applied": "✅", "outreach": "✉️", "shortlisted": "⭐",
                 "replied": "💬", "interview": "🎤", "offer": "🎉",
                 "dismissed": "✕", "rejected": "❌"}
        lines = []
        for _, e in day_ev.iterrows():
            tag = "" if e["status"] == "applied" else f" <i>({esc(e['status'])})</i>"
            lines.append(f'<div class="day-ev">{icons.get(e["status"], "•")} '
                         f'<a href="{esc(e["url"])}">{esc(e["title"])}</a> · '
                         f'{esc(e["company"])}{tag}</div>')
        for _, e in comp_ev.iterrows():
            lines.append(f'<div class="day-ev">{icons.get(e["status"], "•")} '
                         f'{esc(e["name"])} <i>(prospect · {esc(e["status"])})</i></div>')
        st.markdown("".join(lines), unsafe_allow_html=True)


def page_tracker():
    if topic_article():
        return
    page_header("📊 Tracker", "follow-ups first - everything else is bookkeeping")
    now = datetime.now(timezone.utc)
    week = (now - timedelta(days=7)).isoformat()
    applied_cut = (now - timedelta(days=7)).isoformat()
    outreach_cut = (now - timedelta(days=4)).isoformat()
    fu = pd.read_sql_query(
        """SELECT title, company, status, status_updated_at, url FROM jobs
           WHERE (status='applied' AND status_updated_at < ?)
              OR (status='outreach' AND status_updated_at < ?)
           ORDER BY status_updated_at ASC""",
        conn, params=(applied_cut, outreach_cut))
    cfu = pd.read_sql_query(
        """SELECT name AS company, status, status_updated_at FROM companies
           WHERE status='outreach' AND status_updated_at < ?""",
        conn, params=(outreach_cut,))

    st.markdown("#### ⚡ Needs action")
    if fu.empty and cfu.empty:
        st.success("Nothing waiting on you. 🎉")
    else:
        with st.container(border=True):
            for _, r in fu.iterrows():
                days = (now - datetime.fromisoformat(r["status_updated_at"])).days \
                    if r["status_updated_at"] else "?"
                st.markdown(f"- **{r['title']}** - {r['company']} · _{r['status']} "
                            f"{days}d ago, no response - follow up_ · [listing ↗]({r['url']})")
            for _, r in cfu.iterrows():
                days = (now - datetime.fromisoformat(r["status_updated_at"])).days \
                    if r["status_updated_at"] else "?"
                st.markdown(f"- **{r['company']}** (prospect) · _outreach {days}d ago, "
                            f"no response - follow up_")

    st.markdown("#### Pipeline")
    main_statuses = ["new", "shortlisted", "applied", "outreach", "replied",
                     "interview", "offer"]
    counts = {s: conn.execute("SELECT COUNT(*) FROM jobs WHERE status=?", (s,)).fetchone()[0]
              for s in db.JOB_STATUSES}
    cols = st.columns(len(main_statuses))
    for col, s in zip(cols, main_statuses):
        col.metric(s.capitalize(), counts[s])
    st.caption(f"Also: {counts['rejected']} rejected · {counts['dismissed']} dismissed · "
               f"{counts['archived']} archived")

    st.markdown("#### This week")
    stats = {
        "Jobs found": conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE fetched_at > ?", (week,)).fetchone()[0],
        "Applied": conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN ('applied','outreach','replied',"
            "'interview','offer') AND status_updated_at > ?", (week,)).fetchone()[0],
        "Outreach sent": conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN ('outreach','replied') "
            "AND status_updated_at > ?", (week,)).fetchone()[0]
        + conn.execute(
            "SELECT COUNT(*) FROM companies WHERE status IN ('outreach','replied',"
            "'conversation') AND status_updated_at > ?", (week,)).fetchone()[0],
        "Replies (all time)": conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN ('replied','interview','offer')").fetchone()[0]
        + conn.execute(
            "SELECT COUNT(*) FROM companies WHERE status IN ('replied','conversation')").fetchone()[0],
    }
    scols = st.columns(len(stats))
    for col, (k, v) in zip(scols, stats.items()):
        col.metric(k, v)

    st.markdown("#### 📆 Activity")
    _activity_calendar()

    tdf = jobs_df("status NOT IN ('new','archived','dismissed')")
    tdf = tdf.sort_values("status_updated_at", ascending=False)
    with st.expander(f"All tracked jobs ({len(tdf)})"):
        for _, row in tdf.iterrows():
            c1, c2 = st.columns([5.2, 1.15], vertical_alignment="center")
            c1.markdown(f'<div class="trow"><b>{esc(row["title"])}</b> - '
                        f'{esc(row["company"])} · <a href="{esc(row["url"])}">↗</a></div>',
                        unsafe_allow_html=True)
            new_status = c2.selectbox("status", db.JOB_STATUSES,
                                      index=db.JOB_STATUSES.index(row["status"]),
                                      key=f"ts_{row['id']}", label_visibility="collapsed")
            if new_status != row["status"]:
                db.set_status(conn, row["id"], new_status)
                st.rerun()


def _md_section(md: str, header: str) -> str:
    """Text under '## <header>' up to the next '## '."""
    m = re.search(rf"^##\s+{re.escape(header)}\s*$(.*?)(?=^##\s|\Z)",
                  md, re.M | re.S | re.I)
    return m.group(1).strip() if m else ""


def _first_sentences(text: str, n_words: int = 45) -> str:
    words = " ".join(text.split()).split(" ")
    out = " ".join(words[:n_words])
    return out + ("…" if len(words) > n_words else "")


def _session_card(col, tag: str, f, body_html: str):
    with col, st.container(border=True):
        title = f.stem.replace("-", " ").title().replace("Resume ", "Resume drill: ")
        st.markdown(f'<div class="jcard-sub">{tag}</div>'
                    f'<div class="jcard-title">{esc(title)}</div>',
                    unsafe_allow_html=True)
        st.markdown(body_html, unsafe_allow_html=True)
        st.markdown(f'<a href="study?topic={f.stem}" target="_self">📖 open the full topic</a>',
                    unsafe_allow_html=True)


def _todays_session(topic_files, studied):
    """The daily worksheet: solve / learn / rehearse, picked deterministically
    from the oldest unticked topic in each lane. Never empty while anything
    is unstudied - independent of what the routine did today."""
    unticked = [f for f in topic_files if not studied(f)]
    if not unticked:
        st.success("Everything in the study base is ticked off. New material "
                   "lands with the next routine run. 🎉")
        return
    unticked.sort(key=lambda f: f.stat().st_mtime)  # oldest debt first
    dsa = next((f for f in unticked if f.stem.startswith("dsa-")), None)
    drill = next((f for f in unticked if f.stem.startswith("resume-")), None)
    tech = next((f for f in unticked if f is not dsa and f is not drill), None)

    st.markdown("#### 🗓 Today's session")
    cols = st.columns(3, gap="medium")
    if dsa is not None:
        md = dsa.read_text()
        links = re.findall(r"\[([^\]]+)\]\((https?://[^\)]+)\)",
                           _md_section(md, "Practice"))[:3]
        body = "<br>".join(f'<a href="{esc(u)}">{esc(t)}</a>' for t, u in links) \
            or esc(_first_sentences(_md_section(md, "Concept")))
        _session_card(cols[0], "SOLVE · warm-up problems", dsa, body)
    else:
        cols[0].caption("No DSA topic pending.")
    if tech is not None:
        body = esc(_first_sentences(_md_section(tech.read_text(), "Concept")))
        _session_card(cols[1], "LEARN · one concept", tech, body)
    else:
        cols[1].caption("No tech topic pending.")
    if drill is not None:
        probes = re.findall(r"^\s*\d+\.\s+(.{10,140})",
                            _md_section(drill.read_text(), "Interviewer probes"), re.M)[:2]
        body = "<br>".join(f"· {esc(p)}" for p in probes) or "Cross-examination drill."
        _session_card(cols[2], "REHEARSE · resume drill", drill, body)
    else:
        cols[2].caption("No resume drill pending.")
    st.caption("Picked from your oldest unstudied topics. Do these three, tick "
               "them off below, and tomorrow's session moves forward.")
    st.divider()


def page_study():
    if topic_article():
        return
    page_header("📚 Study", "spaced-repetition base built from the jobs you're "
                "applying to - /teach-study in Claude to be taught")
    sdir = study_dir()
    study_md = sdir / "STUDY.md"
    topics_dir = sdir / "topics"
    topic_files = sorted(topics_dir.glob("*.md")) if topics_dir.exists() else []
    if topic_files:
        # ── completion checklist: your pace, not the generator's ──
        progress = db.study_progress_map(conn)

        note_counts = db.study_note_counts(conn)

        def studied(f: Path) -> bool:
            return _studied_at(progress.get(f.stem), f) is not None

        _todays_session(topic_files, studied)

        done_files = [f for f in topic_files if studied(f)]
        todo_files = [f for f in topic_files if not studied(f)]
        c1, c2 = st.columns([3, 1.2], vertical_alignment="center")
        c1.markdown(f"**Checklist** · {len(done_files)}/{len(topic_files)} studied"
                    + (f" · **{len(todo_files)} to go**" if todo_files else " · all caught up 🎉"))
        c2.progress(len(done_files) / len(topic_files))
        with st.container(border=True):
            for f in topic_files:
                done = studied(f)
                updated = (f.stem in progress) and not done
                label = f.stem.replace("-", " ").title()
                if updated:
                    label += "  ·  updated since you studied it"
                r1, r2 = st.columns([6, 2], vertical_alignment="center")
                val = r1.checkbox(label, value=done, key=f"sp_{f.stem}")
                if val != done:
                    db.set_study_done(conn, f.stem, val)
                    st.rerun()
                n_notes = note_counts.get(f.stem, 0)
                extra = f" · {n_notes} note{'s' if n_notes != 1 else ''}" if n_notes else ""
                r2.markdown(f'<div class="topic-open" style="text-align:right">'
                            f'<a href="study?topic={f.stem}" target="_self">📖 read</a>'
                            f'{extra}</div>', unsafe_allow_html=True)
        st.caption("Tick a topic once you've actually worked through it (reading, "
                   "or a /teach-study session). Unticked topics are held by the "
                   "daily routine: they stay due and don't get deepened further "
                   "until you catch up. Each topic opens on its own page, where "
                   "you can highlight text, leave notes, and mark it studied.")

        if study_md.exists():
            with st.expander("Study base overview: revision queue and topic index"):
                st.markdown(linkify_topics(study_md.read_text()), unsafe_allow_html=True)
    elif study_md.exists():
        st.markdown(linkify_topics(study_md.read_text()), unsafe_allow_html=True)
        st.caption("Topic deep-dives will appear here after the daily routine's next run.")
    else:
        st.info("The study base is created by the daily `job-scout-daily-brief` routine - "
                "run it once (Scheduled sidebar → Run now) to start it.")


# ── navigation ──────────────────────────────────────────────────────
pg = st.navigation([
    st.Page(page_today, title="Today", icon="📋", default=True),
    st.Page(page_fresh, title="Fresh matches", icon="🔥", url_path="fresh"),
    st.Page(page_outreach, title="Outreach", icon="✉️", url_path="outreach"),
    st.Page(page_tracker, title="Tracker", icon="📊", url_path="tracker"),
    st.Page(page_study, title="Study", icon="📚", url_path="study"),
])

# global sidebar footer: the one action that isn't page-specific
with st.sidebar:
    st.divider()
    if st.button("⟳ Refresh data", use_container_width=True, disabled=refresh_running()):
        if start_refresh():
            st.toast("Refresh started in the background", icon="⟳")
            st.rerun()
        else:
            st.toast("Refresh disabled or already running", icon="ℹ️")

    @st.fragment(run_every="10s")
    def _refresh_status():
        if refresh_running():
            st.session_state._was_refreshing = True
            st.caption("⟳ fetching in background…")
        else:
            if st.session_state.get("_was_refreshing"):
                st.session_state._was_refreshing = False
                st.rerun(scope="app")
            last = last_updated_epoch(get_conn())
            if last:
                mins = int((datetime.now(timezone.utc).timestamp() - last) / 60)
                ago = f"{mins}m" if mins < 60 else f"{mins // 60}h {mins % 60}m"
                st.caption(f"data updated {ago} ago")

    _refresh_status()

pg.run()
