#!/usr/bin/env python3
"""Job Scout dashboard.  Run:  streamlit run dashboard.py

Multi-page layout: sidebar is navigation (Today lands first), each section owns
the full canvas, and controls live only on the page they affect.
"""
import html as html_mod
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote as urlquote

import pandas as pd
import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from jobscout import cards  # noqa: E402
from jobscout import concepts  # noqa: E402
from jobscout import db  # noqa: E402
from jobscout import graph  # noqa: E402
from jobscout import mapview  # noqa: E402
from jobscout import outreach  # noqa: E402
from jobscout import publish  # noqa: E402
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
# The graph reads the same shapes to build its edges, so there is one
# pattern, and it lives with the graph.
_TOPIC_MD_LINK = graph.TOPIC_MD_LINK


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
    if not graph.is_studied(completed_at, f):
        return None
    return datetime.fromisoformat(completed_at)


@st.cache_data(ttl=60, show_spinner=False)
def _concept_graph_for(signature):
    """The concept layer for one state of the topic folder. The signature is
    the cache key and nothing else: it is what tells Streamlit that a file
    was rewritten, added or removed since the last build."""
    return concepts.build(study_dir(), conn)


def _concept_graph():
    """`concepts.build` over the study folder, built at most once per change
    to it (and once a minute regardless, so an override written by another
    tab is picked up).

    Every page that wants prerequisites calls this rather than `build`, which
    walks and parses every topic file. What the cache must NOT hold is which
    topics are ticked off: ticking a checkbox writes the DB and leaves the
    folder alone, so the signature would not move. Studied state comes from
    `concepts.study_order(result, conn)` with a live connection instead.
    """
    topics = study_dir() / "topics"
    try:
        signature = tuple(sorted((f.name, f.stat().st_mtime)
                                 for f in topics.glob("*.md")))
    except OSError:
        signature = ()
    # prerequisite overrides live in the DB, not the folder, and change the
    # effective graph the moment one is written: they are part of the key
    overrides = tuple(sorted((slug, prereq, action)
                             for slug, m in db.overrides_map(conn).items()
                             for prereq, action in m.items()))
    return _concept_graph_for((signature, overrides))


def _back_to_study():
    st.markdown('<a href="study" target="_self">← Back to Study</a>',
                unsafe_allow_html=True)


def _concept_chips(slug: str, result: dict):
    """What this topic teaches, as chips under the Related strip.

    The names come from the topic's own `concepts` frontmatter and stay in
    the order they were written - that order is the author's, and sorting it
    would lose the "start here, end there" reading of a curated list. The
    spelling, though, is the canonical one from the concept graph, so a
    topic that wrote "KV-cache paging" where an earlier topic wrote "paged
    KV cache" still shows one name for one idea.

    Each chip links at the concept sheet page (`/concepts?c=<id>`, WS-C).
    That page may not exist yet; a chip that goes nowhere for a few days is
    a better shape to build than a chip that has to be rewired later.

    A topic with no concepts - every resume drill, and any technical topic
    the routine has not caught up with - renders nothing at all.
    """
    names = (result["topics"].get(slug) or {}).get("concepts") or []
    chips = []
    for name in names:
        key = concepts.normalise(name)
        if not key:
            continue                           # a name that is all stop words
        entry = result["concepts"].get(key)
        label = entry["canonical"] if entry else name
        cid = entry["id"] if entry else concepts.concept_id(key)
        chips.append(f'<a class="chip" href="concepts?c={urlquote(cid)}" '
                     f'target="_self">{esc(label)}</a>')
    if not chips:
        return
    st.markdown(f'<div class="topic-concepts page-sub" '
                f'style="margin:.1rem 0 .7rem 0">Concepts: {"".join(chips)}</div>',
                unsafe_allow_html=True)


def _prereq_strip(slug: str, f: Path, result: dict):
    """What to read before this topic, and your corrections to that list.

    The routine proposes `prereqs` in the frontmatter and rewrites the file
    every night, so a correction made in the file would not survive until
    morning. Corrections are made here instead and live in the DB: the `x`
    on a prerequisite records a "remove" (or simply forgets an "add", when
    that is how it got on the list), a removed frontmatter prerequisite
    stays visible struck through with a "restore" next to it, and the picker
    records an "add". `jobscout.concepts` reads the three back as frontmatter
    minus the removes plus the adds.

    The picker never offers a topic that already needs this one, at any
    remove - `concepts.would_cycle` walks the effective graph, not the
    declared one, so a cycle you could only create through your own earlier
    overrides is refused too.

    Resume drills unlock nothing and need nothing, so they get no strip.
    """
    try:
        meta, _ = graph.split_frontmatter(f.read_text())
    except OSError:
        return
    if graph.track_of(slug, meta) == "resume":
        return

    topics = result["topics"]
    effective = list(result["prereqs"].get(slug, ()))
    mine = result["overrides"].get(slug, {})
    removed = [p for p in (topics.get(slug) or {}).get("prereqs", ())
               if mine.get(p) == "remove" and p in topics]

    def title_of(p):
        return (topics.get(p) or {}).get("title", p)

    def write(action, prereq):
        """Every write opens its own connection: a button's rerun can land on
        a different thread than the one that opened the module-level one, and
        sqlite objects are bound to their thread (same reason as on_action)."""
        c = get_conn()
        try:
            if action == "clear":
                db.clear_override(c, slug, prereq)
            else:
                db.add_override(c, slug, prereq, action)
        except (sqlite3.Error, ValueError):
            pass
        finally:
            c.close()

    rows = [(p, False) for p in effective] + [(p, True) for p in removed]
    if rows:
        cols = st.columns([1.5] + [2.2, 0.8] * len(rows),
                          vertical_alignment="center")
        cols[0].markdown('<div class="topic-prereqs page-sub" '
                         'style="margin:0">Prerequisites:</div>',
                         unsafe_allow_html=True)
        for i, (prereq, gone) in enumerate(rows):
            link, act = cols[1 + 2 * i], cols[2 + 2 * i]
            style = ' style="text-decoration:line-through; opacity:.55"' if gone else ""
            cls = "prereq-gone" if gone else "prereq-live"
            link.markdown(f'<a class="{cls}"{style} href="study?topic={prereq}" '
                          f'target="_self">📖 {esc(title_of(prereq))}</a>',
                          unsafe_allow_html=True)
            if gone:
                if act.button("restore", key=f"prq_re_{slug}_{prereq}",
                              help=f"Put '{title_of(prereq)}' back; the "
                                   f"frontmatter has the last word again"):
                    write("clear", prereq)
                    st.rerun()
            elif act.button("✕", key=f"prq_rm_{slug}_{prereq}",
                            help=f"This topic does not need "
                                 f"'{title_of(prereq)}' first"):
                # an "add" of your own is forgotten rather than contradicted:
                # a "remove" over it would outlive the frontmatter it was
                # never in and quietly block the routine from proposing it
                write("clear" if mine.get(prereq) == "add" else "remove", prereq)
                st.rerun()
    else:
        st.caption("Prerequisites: none declared")

    taken = set(effective) | {slug}
    options = [p for p in sorted(topics)
               if p not in taken
               and topics[p].get("track") != "resume"
               and not concepts.would_cycle(result, slug, p)]
    pick, add = st.columns([3, 1], vertical_alignment="bottom")
    chosen = pick.selectbox(
        "add a prerequisite", options, index=None, key=f"prq_add_{slug}",
        format_func=title_of, placeholder="choose a topic",
        help="Only topics that do not already need this one: a prerequisite "
             "loop would leave the study order unreadable.")
    if add.button("Add", key=f"prq_add_btn_{slug}", disabled=not chosen):
        write("add", chosen)
        st.rerun()


def _diagram(slug: str):
    """The topic's overview illustration, above the deck: one picture of the
    mental model before any words.

    The picture is `study/diagrams/<slug>.svg`, exported from the Excalidraw
    scene next to it (see tools/README.md). The scene is the source you edit;
    the SVG is what every surface embeds. A topic without one renders nothing
    at all - no placeholder, no empty box - because most topics will not have
    a diagram for a while and an empty frame on every page is worse than no
    frame.
    """
    svg = study_dir() / "diagrams" / f"{slug}.svg"
    if not svg.exists():
        return
    with st.container(border=True):
        st.markdown('<div class="page-sub" style="margin:.1rem 0 .3rem 0">'
                    '🖼 Overview</div>', unsafe_allow_html=True)
        st.image(str(svg), width="stretch")
        source = svg.with_suffix(".excalidraw")
        if source.exists():
            st.caption(f"Editable source: `study/diagrams/{source.name}` "
                       f"(open it at excalidraw.com, then re-export)")


def _publish_control(slug: str, f: Path):
    """"Publish this version" for the archive site, next to "Mark as studied".

    The flag records the sha256 of the file you have just read, not the fact
    that you liked the topic: the daily routine rewrites these files, so an
    approval that outlived its version would publish prose nobody read. When
    the file has moved since, the toggle stays on but the export holds the
    topic back, and this says so and offers the one-click re-approval.

    Resume drills get the control disabled rather than hidden, because the
    reason they can never be published is worth reading once.
    """
    try:
        text = f.read_text()
    except OSError:
        return
    meta, _ = graph.split_frontmatter(text)
    track = graph.track_of(slug, meta)
    key = f"publish_{slug}"
    row = db.publish_map(conn).get(slug)

    c1, c2 = st.columns([1.4, 3], vertical_alignment="center")
    if track == "resume":
        c1.toggle("Publish this version", value=False, disabled=True, key=key,
                  help="Resume drills are never published: they quote your own "
                       "claims back at you and belong to you alone.")
        c2.caption("Resume drills stay private. The exporter excludes them by "
                   "track and refuses any page that links to one.")
        return

    def on_toggle():
        # Widget callbacks can run on a different thread than the one that
        # opened the module-level connection, and sqlite objects are bound to
        # their thread - same reason on_action opens its own (see below).
        c = get_conn()
        try:
            if st.session_state.get(key):
                db.set_publish(c, slug, publish.content_hash(f.read_text()))
            else:
                db.clear_publish(c, slug)
        except OSError:
            pass
        finally:
            c.close()

    c1.toggle("Publish this version", value=bool(row), key=key,
              on_change=on_toggle,
              help="Exports this exact version to the archive site on the next "
                   "`python -m jobscout.publish export`.")
    if not row:
        c2.caption("Off by default. Nothing leaves the study folder until you "
                   "have read a version and ticked it.")
    elif row.get("content_hash") != publish.content_hash(text):
        c2.caption(":orange[⚠ the published version differs from the current "
                   "file] - the routine has deepened this topic since you "
                   "reviewed it, so the export is holding it back.")
        if c2.button("Re-review and publish this version",
                     key=f"republish_{slug}"):
            db.set_publish(conn, slug, publish.content_hash(text))
            st.toast("This version is now the published one", icon="✅")
            st.rerun()
    else:
        c2.caption("This version is approved. The next export publishes it; "
                   "unticking deletes it from the site.")


def _flashcards(slug: str):
    """The topic's deck, between the Related strip and the article: every
    question visible, every answer folded away until you have had a go at it.

    The deck is a sidecar file (see jobscout.cards), so it appears here and
    nowhere else on the page - the article body never contains one. Shuffle
    stores a seed per topic in session state, which makes the order stable
    across reruns and different only when you ask for a different order.
    """
    deck = cards.load(study_dir(), slug)
    if not deck:
        return
    seed_key = f"deck_seed_{slug}"
    seed = st.session_state.get(seed_key, 0)
    order = list(range(len(deck)))
    if seed:
        random.Random(seed).shuffle(order)

    head, shuffle = st.columns([4, 1], vertical_alignment="center")
    head.markdown(f'<div class="page-sub" style="margin:.1rem 0 .2rem 0">'
                  f'🃏 Flashcards · {len(deck)}</div>', unsafe_allow_html=True)
    if shuffle.button("Shuffle", key=f"deck_shuffle_{slug}",
                      help="Reorder the deck; answering out of order is the test"):
        st.session_state[seed_key] = random.randrange(1, 1_000_000)
        st.rerun()
    for i in order:
        with st.expander(deck[i]["q"]):
            st.markdown(deck[i]["a"])


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

    # ── neighbourhood: where this topic sits in the study graph ──
    # Both directions: what this topic points at, and what points back at
    # it. A topic nobody links to is one you never stumble onto again.
    rel = graph.neighbors(graph.build(study_dir(), conn), slug)
    if rel:
        links = " · ".join(
            f'<a href="study?topic={n["slug"]}" target="_self">📖 {esc(n["title"])}</a>'
            for n in rel)
        st.markdown(f'<div class="topic-related page-sub" '
                    f'style="margin:.1rem 0 .7rem 0">Related: {links}</div>',
                    unsafe_allow_html=True)

    # ── the concept layer: what this topic teaches, and what comes first ──
    # One build for both strips, and the only place this page walks the
    # concept graph (WS-B1's cached `_concept_graph()` swaps in here).
    cresult = _concept_graph()
    _concept_chips(slug, cresult)
    _prereq_strip(slug, f, cresult)

    _diagram(slug)
    _flashcards(slug)

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
    _publish_control(slug, f)
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


def _body(f: Path) -> str:
    """A topic file's prose, with the frontmatter block left behind - the
    worksheet quotes topics, and YAML is not a concept summary."""
    return graph.split_frontmatter(f.read_text())[1]


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


def _after_line(row, titles) -> str:
    """The muted "after: …" line under a picked card, or nothing.

    A topic can be the best thing to read today and still owe you something -
    the starvation escape promotes a topic whose prerequisites are unstudied,
    and a chain a -> b -> c with b studied leaves c ready but still owing a.
    The line names that debt without demoting the card.
    """
    after = row.get("after") if row else None
    if not after:
        return ""
    names = ", ".join(titles.get(slug, slug) for slug in after)
    return (f'<div class="jcard-sub" style="margin:8px 0 0 0">'
            f'after: {esc(names)}</div>')


def _todays_session(topic_files, studied, order_rows):
    """The daily worksheet: solve / learn / rehearse, one card a lane.

    The two technical lanes take the first topic of their kind out of
    `concepts.study_order`, so what you are handed is the readiest thing you
    have not read: prerequisites first, and among topics that are equally
    ready, the one that has waited longest. Resume drills carry no
    prerequisites and are not in the order at all, so the drill lane keeps
    the old rule - the oldest unticked drill.

    Never empty while anything is unstudied, independent of what the routine
    did today.
    """
    unticked = [f for f in topic_files if not studied(f)]
    if not unticked:
        st.success("Everything in the study base is ticked off. New material "
                   "lands with the next routine run. 🎉")
        return
    unticked.sort(key=lambda f: f.stat().st_mtime)  # oldest debt first
    drill = next((f for f in unticked if f.stem.startswith("resume-")), None)

    by_slug = {f.stem: f for f in unticked}
    rows = [r for r in order_rows if r["slug"] in by_slug]
    titles = {r["slug"]: r["title"] for r in order_rows}
    dsa_row = next((r for r in rows if r["slug"].startswith("dsa-")), None)
    tech_row = next((r for r in rows if not r["slug"].startswith("dsa-")
                     and not r["slug"].startswith("resume-")), None)
    dsa = by_slug[dsa_row["slug"]] if dsa_row else None
    tech = by_slug[tech_row["slug"]] if tech_row else None

    st.markdown("#### 🗓 Today's session")
    cols = st.columns(3, gap="medium")
    if dsa is not None:
        md = _body(dsa)
        links = re.findall(r"\[([^\]]+)\]\((https?://[^\)]+)\)",
                           _md_section(md, "Practice"))[:3]
        body = "<br>".join(f'<a href="{esc(u)}">{esc(t)}</a>' for t, u in links) \
            or esc(_first_sentences(_md_section(md, "Concept")))
        _session_card(cols[0], "SOLVE · warm-up problems", dsa,
                      body + _after_line(dsa_row, titles))
    else:
        cols[0].caption("No DSA topic pending.")
    if tech is not None:
        body = esc(_first_sentences(_md_section(_body(tech), "Concept")))
        _session_card(cols[1], "LEARN · one concept", tech,
                      body + _after_line(tech_row, titles))
    else:
        cols[1].caption("No tech topic pending.")
    if drill is not None:
        probes = re.findall(r"^\s*\d+\.\s+(.{10,140})",
                            _md_section(_body(drill), "Interviewer probes"), re.M)[:2]
        body = "<br>".join(f"· {esc(p)}" for p in probes) or "Cross-examination drill."
        _session_card(cols[2], "REHEARSE · resume drill", drill, body)
    else:
        cols[2].caption("No resume drill pending.")
    st.caption("Picked by readiness: prerequisites first, then what has "
               "waited longest.")
    st.divider()


def page_study():
    if topic_article():
        return
    page_header("📚 Study", "spaced-repetition base built from the jobs you're "
                "applying to - /tutor in Claude to be taught")
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

        # readiness, from the cached concept layer plus live ticks: what the
        # worksheet picks with, and what the checklist hints with
        order_rows = concepts.study_order(_concept_graph(), conn)["order"]
        order_by_slug = {r["slug"]: r for r in order_rows}

        _todays_session(topic_files, studied, order_rows)

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
                # the list stays alphabetical - it is a checklist, not a queue -
                # so a topic that needs something else first says so instead
                row = order_by_slug.get(f.stem)
                if not done and row and row["after"]:
                    first = row["after"][0]
                    label += ("  ·  after: "
                              + order_by_slug.get(first, {}).get("title", first))
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
                   "or a /tutor session). Unticked topics are held by the "
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
    st.caption('Every idea these topics teach, with a one-line definition where '
               'a flashcard gives one, is on the '
               '<a href="concepts" target="_self">Concepts</a> sheet.',
               unsafe_allow_html=True)


def page_map():
    if topic_article():
        return
    page_header("🗺 Map", "the study base as a picture: every topic a node, "
                "coloured by track, linked by what it points at")
    g = graph.build(study_dir(), conn)
    if not g["nodes"]:
        st.info("Nothing to draw yet - the study base is created by the daily "
                "`job-scout-daily-brief` routine.")
        return

    tracks = sorted({n["track"] for n in g["nodes"]})
    c1, c2, c3 = st.columns([2.4, 1.3, 1.1], vertical_alignment="center")
    with c1:
        chosen = st.pills("Tracks", tracks, selection_mode="multi",
                          default=tracks, key="map_tracks",
                          label_visibility="collapsed")
    with c2:
        # Topics is the default and stays the default: concept mode adds a
        # satellite per idea, which is five times the nodes, and that is a
        # thing you ask for rather than a thing that happens to you.
        mode = st.segmented_control("Mode", ["Topics", "Concepts"],
                                    default="Topics", key="map_mode",
                                    label_visibility="collapsed")
    with c3:
        unstudied = st.toggle("Unstudied only", key="map_unstudied")

    if mode == "Concepts":
        g = mapview.with_concepts(g, _concept_graph())
    view = mapview.filter_graph(g, tracks=list(chosen or []),
                                unstudied_only=unstudied)
    topics = [n for n in view["nodes"] if not mapview.is_concept(n)]
    sats = [n for n in view["nodes"] if mapview.is_concept(n)]
    arrows = sum(1 for e in view["edges"] if e.get("kind") == "prereq")
    links = sum(1 for e in view["edges"]
                if e.get("kind") not in ("prereq", "concept"))
    n_done = sum(1 for n in topics if n["studied"])
    bits = [f"{len(topics)} topic{'s' if len(topics) != 1 else ''}",
            f"{links} link{'s' if links != 1 else ''}"]
    if sats:
        bits.append(f"{len(sats)} concept{'s' if len(sats) != 1 else ''}")
        bits.append(f"{arrows} prerequisite{'s' if arrows != 1 else ''}")
    bits.append(f"{n_done} studied")
    st.caption(" · ".join(bits))
    if not topics:
        st.info("No topics match these filters. Pick a track back up, or turn "
                "off \"Unstudied only\".")
        return

    key = "study_map"
    pins = getattr(st.session_state.get(key), "pins", None) or {}
    mapview.study_map(view, pins=pins, key=key)
    tail = ("Concept mode hangs every idea off the topic that teaches it, and "
            "an idea two topics share sits between them - hover one to light "
            "up everybody who names it, or click it to open its line on the "
            "Concepts sheet. The arrows are prerequisites and point from what "
            "you read first to what it unlocks; the topics themselves do not "
            "move between the two modes. "
            if mode == "Concepts" else "")
    st.caption(tail +
               "The map drifts on its own. Hover a node to light up what it "
               "connects to, click to open the topic, drag one around and its "
               "neighbours follow; letting go pins it (pins survive reruns) "
               "and a double-click sets it loose again. A solid ring means "
               "studied, a dashed outline means it is still waiting on you, "
               "the circle grows with how many questions the topic asks, and "
               "the badge counts your notes.")


def _scroll_to_concept(param: str):
    """Scroll `?c=<anchor>` into view once the sheet is on the page.

    The sheet gives every concept an `<a id>` on its first appearance, and a
    chip or a map node links here with that id. Streamlit renders the page
    top-down and React commits when it is ready, so the script retries for a
    few seconds rather than assuming the markdown has landed (the first tick
    reliably misses), and gives up quietly: a stale link should leave you at
    the top of the sheet, not staring at an error.

    The script contains no `<` on purpose. `st.html` hands the body to the
    browser as markup, so a `for (i < n)` reads as the start of a tag and the
    whole script is dropped without a word - hence forEach and `40 > n`.
    """
    # links carry the concept id ("c:alpha concept"); drop the namespace
    # first, or the colon strip below welds it onto the name
    raw = re.sub(r"[^a-zA-Z0-9 _-]", "", concepts.anchor_for(param)).strip().replace(" ", "-")
    if not raw:
        return
    # a link may carry the anchor, or the concept name it was made from
    guess = concepts.anchor_for(concepts.normalise(raw.replace("-", " ")))
    wanted = [w for w in dict.fromkeys([raw, guess]) if w]
    st.html(
        "<script>(function(){var ids=" + json.dumps(wanted) + ",n=0;"
        "function tick(){var hit=null;"
        "ids.forEach(function(id){if(!hit){hit=document.getElementById(id);}});"
        "if(hit){hit.scrollIntoView({block:'center'});"
        "var row=hit.parentElement;if(row){"
        "row.style.background='rgba(255,214,0,.22)';"
        "row.style.borderRadius='4px';}return;}"
        "n=n+1;if(40>n){setTimeout(tick,100);}}tick();})();</script>",
        unsafe_allow_javascript=True)


def page_concepts():
    """The generated concept sheet as its own page.

    The same text `python -m jobscout.concepts sheet` writes to
    study/CONCEPTS.md, so what you read here and what the routine reads
    before it names a concept are one document. Topic headings are rewritten
    into topic-page links on the way through, which is why the sheet writes
    them as `topics/<slug>.md` in the first place.
    """
    if topic_article():
        return
    page_header("🧭 Concepts", "the vocabulary of the study base: every topic's "
                "concepts, defined where a flashcard defines them")
    sdir = study_dir()
    result = concepts.build(sdir, conn)
    if not result["topics"]:
        st.info("Nothing to list yet - the study base is created by the daily "
                "`job-scout-daily-brief` routine.")
        return

    md = concepts.sheet(result, sdir, conn)
    md = re.sub(r"\A#[ \t]+Concepts[ \t]*\n", "", md)     # the header says it
    st.markdown(linkify_topics(md), unsafe_allow_html=True)
    _scroll_to_concept(st.query_params.get("c") or "")
    st.caption("Concepts come from each topic's frontmatter and definitions "
               "from its flashcard deck, so \"(no card yet)\" is a real gap: "
               "the deck has no card that says what the thing is. Nothing on "
               "this page is hand-written - regenerate it with "
               "`python -m jobscout.concepts sheet`.")


# ── navigation ──────────────────────────────────────────────────────
pg = st.navigation([
    st.Page(page_today, title="Today", icon="📋", default=True),
    st.Page(page_fresh, title="Fresh matches", icon="🔥", url_path="fresh"),
    st.Page(page_outreach, title="Outreach", icon="✉️", url_path="outreach"),
    st.Page(page_tracker, title="Tracker", icon="📊", url_path="tracker"),
    st.Page(page_study, title="Study", icon="📚", url_path="study"),
    st.Page(page_map, title="Map", icon="🗺️", url_path="map"),
    st.Page(page_concepts, title="Concepts", icon="🧭", url_path="concepts"),
])

# global sidebar footer: the one action that isn't page-specific
with st.sidebar:
    st.divider()
    if st.button("⟳ Refresh data", width="stretch", disabled=refresh_running()):
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
