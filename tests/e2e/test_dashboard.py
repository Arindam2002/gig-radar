"""Playwright E2E against the seeded dashboard (see conftest.seed for data)."""
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from conftest import ROOT, db_conn, goto_page, job_status


def test_nav_and_refresh_button(dash, server):
    # sidebar nav shows all five pages; global refresh button lives there too
    for name in ("Today", "Fresh matches", "Outreach", "Tracker", "Study"):
        assert dash.locator(f"text={name} >> visible=true").count() >= 1, name
    btn = dash.locator("button", has_text="Refresh data").first
    assert btn.is_visible()
    btn.click()
    dash.wait_for_timeout(800)
    # disabled via JOBSCOUT_DISABLE_REFRESH: no crash, page still alive
    assert dash.locator("text=Fresh matches >> visible=true").count() >= 1


def card_of(dash, title):
    """Card row holding the given job title (score + body + action buttons
    share one horizontal block)."""
    return dash.locator("div[data-testid='stHorizontalBlock']", has_text=title).last


def test_fresh_matches_render_and_filter(dash, server):
    # high-score fixture visible, low-score filtered out at default min=40
    assert dash.locator("text=AI Infrastructure Engineer >> visible=true").count() >= 1
    assert dash.locator("text=Desktop Support Engineer >> visible=true").count() == 0
    # salary chip renders without NaN; card shows score badge and chips
    assert dash.locator("text=₹35–45 LPA").count() >= 1
    card_text = card_of(dash, "AI Infrastructure Engineer").inner_text().lower()
    assert "nan" not in card_text
    assert "sarvam" in card_text


def test_shortlist_persists_to_db(dash, server):
    card_of(dash, "AI Infrastructure Engineer").locator(
        "button", has_text="Shortlist").first.click()
    dash.wait_for_timeout(1500)
    assert job_status(server, "AI Infrastructure Engineer") == "shortlisted"
    # cleanup for other tests: back to new
    conn = db_conn(server)
    conn.execute("UPDATE jobs SET status='new' WHERE title='AI Infrastructure Engineer'")
    conn.commit()
    conn.close()


def test_render_marks_seen(dash, server):
    # cards mark themselves seen when displayed in the list
    dash.wait_for_timeout(500)
    conn = db_conn(server)
    row = conn.execute(
        "SELECT seen_at FROM jobs WHERE title='LLM Platform Engineer'").fetchone()
    conn.close()
    assert row["seen_at"] is not None


def test_empty_filter_result_no_crash(dash, server):
    """A filter combo yielding zero rows before the salary check must show the
    empty state, not KeyError:'score' from pandas apply() on an empty frame
    (regression: user selected a source with no rows)."""
    slider = dash.locator("input[type='range']").first  # Min match score
    slider.press("End")  # -> 100, nothing scores that high
    dash.wait_for_timeout(1500)
    assert dash.locator("text=KeyError").count() == 0
    assert dash.locator("text=Traceback").count() == 0
    assert dash.locator("text=No matches with the current filters >> visible=true").count() >= 1
    # reset for other tests
    slider.press("Home")
    dash.wait_for_timeout(1200)


def test_abroad_market_filter(dash, server):
    """Abroad segment shows foreign-restricted roles with relocation chip."""
    dash.get_by_text("Abroad", exact=True).first.click()
    dash.wait_for_timeout(1500)
    assert dash.locator("text=N26 >> visible=true").count() >= 1
    assert dash.locator("text=visa/relocation mentioned >> visible=true").count() >= 1
    # India-only rows are excluded from the Abroad view
    assert dash.locator("text=Sarvam AI >> visible=true").count() == 0
    dash.get_by_text("All", exact=True).first.click()
    dash.wait_for_timeout(1000)


def test_topic_link_opens_topic_page(page, server):
    """?topic=<slug> (rewritten from file links) opens the topic's own page
    from any tab: the article, a back link and the studied control, nothing
    else from the Study page (regression: raw file links were dead)."""
    page.goto(server["url"] + "/?topic=demo-alpha-topic")
    page.wait_for_selector("text=Back to Study", timeout=30000)
    assert page.locator("#jsr-art h1", has_text="Demo Alpha Topic").count() == 1
    assert page.locator("text=Mark as studied >> visible=true").count() >= 1
    assert page.locator("text=Today's session >> visible=true").count() == 0
    # sibling links written as bare "<slug>.md" inside a topic file used to
    # 404 as /study/<slug>.md - now they route to the topic page
    beta = page.locator("#jsr-art a", has_text="beta")
    assert beta.get_attribute("href") == "study?topic=demo-beta-topic"
    # a non-topic .md link is left as written
    assert page.locator("#jsr-art a", has_text="notes").get_attribute("href") == "../session-notes.md"
    beta.click()
    page.wait_for_selector("#jsr-art h1:has-text('Demo Beta Topic')", timeout=30000)
    assert "topic=demo-beta-topic" in page.url
    assert page.locator("text=Page not found").count() == 0


def test_topic_page_hides_frontmatter(page, server):
    """The YAML block is metadata for the graph, not prose: the article opens
    on its H1, with no stray '---' rule and no `track:` line in the text."""
    goto_page(page, server, "/study?topic=demo-alpha-topic")
    page.wait_for_selector("#jsr-art h1", timeout=30000)
    text = page.locator("#jsr-art").inner_text().strip()
    assert not text.startswith("---")
    assert "track:" not in text and "related:" not in text
    assert page.locator("#jsr-art h1").first.inner_text() == "Demo Alpha Topic"
    assert text.startswith("Demo Alpha Topic")


def test_topic_page_related_strip(page, server):
    """The Related strip is the neighbourhood in both directions: alpha
    declares beta (outbound), so beta's page must show alpha (inbound)."""
    goto_page(page, server, "/study?topic=demo-alpha-topic")
    page.wait_for_selector("div.topic-related", timeout=30000)
    strip = page.locator("div.topic-related").first
    assert strip.inner_text().startswith("Related:")
    assert strip.locator("a[href='study?topic=demo-beta-topic']").count() == 1

    goto_page(page, server, "/study?topic=demo-beta-topic")
    page.wait_for_selector("div.topic-related", timeout=30000)
    back = page.locator("div.topic-related").first
    assert back.locator("a[href='study?topic=demo-alpha-topic']").count() == 1


def test_topic_page_deck(page, server):
    """The sidecar deck renders once, above the article, with its answers
    folded: every question appears exactly once in the DOM (the article body
    never carries a deck, so nothing renders twice), an answer is hidden
    until its card is opened, and a topic without a deck shows no deck."""
    goto_page(page, server, "/study?topic=demo-alpha-topic")
    page.wait_for_selector("text=Flashcards", timeout=30000)
    q1 = "What does the alpha fixture deck prove?"
    q2 = "Where does a deck live?"
    assert page.locator(f"text={q1}").count() == 1
    assert page.locator(f"text={q2}").count() == 1

    answer = page.locator("text=Alphacardanswerone").first
    assert not answer.is_visible()               # folded until you have a go
    page.get_by_text(q1, exact=True).click()
    page.wait_for_timeout(800)
    assert answer.is_visible()

    # shuffling reseeds the order; the deck is still the same two cards
    page.locator("button", has_text="Shuffle").first.click()
    page.wait_for_timeout(1500)
    assert page.locator(f"text={q1}").count() == 1
    assert page.locator(f"text={q2}").count() == 1

    # beta has no sidecar file, so the page carries no deck at all
    goto_page(page, server, "/study?topic=demo-beta-topic")
    page.wait_for_selector("#jsr-art h1", timeout=30000)
    assert page.locator("text=Flashcards").count() == 0


def test_topic_page_highlight_note_and_studied(page, server):
    """Selecting text in the article offers Highlight; the highlight persists
    to study_notes, re-renders as a <mark>, takes a note, and the page's
    mark-as-studied button writes study_progress."""
    goto_page(page, server, "/study?topic=demo-beta-topic")
    page.wait_for_selector("#jsr-art h1", timeout=30000)
    page.evaluate("""() => {
        const art = document.querySelector('#jsr-art');
        const tn = art.querySelector('p').firstChild;
        const r = document.createRange(); r.setStart(tn, 0); r.setEnd(tn, 4);
        const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
        art.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
    }""")
    page.wait_for_selector("#jsr-bar:not([hidden])", timeout=5000)
    page.locator("#jsr-bar [data-act=hl]").click()
    page.wait_for_selector("mark.jsr-hl", timeout=15000)
    assert page.locator("mark.jsr-hl").first.inner_text() == "test"
    conn = db_conn(server)
    row = conn.execute("SELECT quote, note FROM study_notes WHERE slug=?",
                       ("demo-beta-topic",)).fetchone()
    assert row is not None and row["quote"] == "test" and row["note"] == ""
    # click the highlight -> note popover -> save
    page.locator("mark.jsr-hl").first.click()
    page.wait_for_selector("#jsr-pop:not([hidden])", timeout=5000)
    page.locator("#jsr-pop-text").fill("remember this")
    page.locator("#jsr-pop [data-act=save]").click()
    page.wait_for_selector("mark.jsr-hl.jsr-noted", timeout=15000)
    assert page.locator("text=remember this >> visible=true").count() >= 1
    assert conn.execute("SELECT note FROM study_notes WHERE slug=?",
                        ("demo-beta-topic",)).fetchone()["note"] == "remember this"
    # mark studied from the bottom of the article
    page.locator("button", has_text="Mark as studied").first.click()
    page.wait_for_selector("text=Mark as not studied", timeout=15000)
    assert conn.execute("SELECT completed_at FROM study_progress WHERE slug=?",
                        ("demo-beta-topic",)).fetchone()["completed_at"]
    # clean up so the checklist test still starts at 0/2
    page.locator("button", has_text="Mark as not studied").first.click()
    page.wait_for_selector("text=Mark as studied", timeout=15000)
    conn.execute("DELETE FROM study_notes WHERE slug=?", ("demo-beta-topic",))
    conn.commit()
    conn.close()


def test_brief_actions_apply_from_today_page(page, server):
    """Brief picks carry inline status actions - mark applied without hunting
    the job down in Fresh matches."""
    goto_page(page, server, "/")
    # DB-matched pick has action buttons; unknown external link has none
    # numbered-bold pick format must also get action buttons
    glean_row = page.locator("div[data-testid='stHorizontalBlock']",
                             has_text="LLM Platform Engineer").last
    assert glean_row.get_by_text("⭐", exact=True).count() >= 1
    # link-on-its-own-line format ("[Job posting](url)") gets buttons too
    jp_row = page.locator("div[data-testid='stHorizontalBlock']",
                          has_text="Job posting").last
    assert jp_row.get_by_text("⭐", exact=True).count() >= 1
    # a URL only the manifest knows resolves via title+company (Fractal is
    # shortlisted, so it renders a status chip rather than buttons)
    man_row = page.locator("div[data-testid='stHorizontalBlock']",
                           has_text="Manifest-only pick").last
    assert man_row.get_by_text("✓ shortlisted").count() >= 1
    row = page.locator("div[data-testid='stHorizontalBlock']",
                       has_text="AI Infrastructure Engineer").last
    row.get_by_text("✓", exact=True).first.click()
    page.wait_for_timeout(1500)
    assert job_status(server, "AI Infrastructure Engineer") == "applied"
    # after acting, the row shows a status chip instead of buttons
    assert page.locator("text=✓ applied >> visible=true").count() >= 1
    conn = db_conn(server)
    conn.execute("UPDATE jobs SET status='new' WHERE title='AI Infrastructure Engineer'")
    conn.commit()
    conn.close()


def test_sidebar_reopens_after_collapse(dash, server):
    """Collapsing the sidebar must leave a visible reopen control (regression:
    the expand button lived inside the hidden Streamlit header)."""
    dash.locator("[data-testid='stSidebar']").hover()  # button is hover-revealed
    dash.wait_for_timeout(400)
    dash.locator("[data-testid='stSidebarCollapseButton'] button").first.click(force=True)
    dash.wait_for_timeout(800)
    reopen = dash.locator("[data-testid='stExpandSidebarButton']")
    assert reopen.first.is_visible(), "reopen control is not visible after collapse"
    reopen.first.click()
    dash.wait_for_timeout(800)
    assert dash.locator("text=Fresh matches >> visible=true").count() >= 1


def test_study_checklist_tracks_completion(dash, server):
    """Study page shows a completion checklist; ticking a topic persists to
    the study_progress table and updates the counter."""
    goto_page(dash, server, "/study")
    # the daily worksheet renders above the checklist (learn lane from the
    # seeded demo topics; dsa/resume lanes empty in fixtures)
    assert dash.locator("text=Today's session >> visible=true").count() >= 1
    assert dash.locator("text=LEARN >> visible=true").count() >= 1
    assert dash.locator("text=Checklist >> visible=true").count() >= 1
    assert dash.locator("text=0/2 studied >> visible=true").count() >= 1
    # every checklist row links to its topic page; no topic selectbox anymore
    assert dash.locator("a[href='study?topic=demo-alpha-topic']").count() >= 1
    assert dash.locator("text=Overview (STUDY.md)").count() == 0
    dash.locator("div[data-testid='stCheckbox']", has_text="Demo Alpha Topic").first.click()
    dash.wait_for_timeout(1500)
    assert dash.locator("text=1/2 studied >> visible=true").count() >= 1
    conn = db_conn(server)
    row = conn.execute("SELECT completed_at FROM study_progress WHERE slug=?",
                       ("demo-alpha-topic",)).fetchone()
    conn.close()
    assert row is not None and row["completed_at"]
    # untick to leave state clean for other tests
    dash.locator("div[data-testid='stCheckbox']", has_text="Demo Alpha Topic").first.click()
    dash.wait_for_timeout(1200)
    assert dash.locator("text=0/2 studied >> visible=true").count() >= 1


def test_activity_calendar(dash, server):
    """Tracker shows the clickable month grid; the selected day (today by
    default) lists that day's status events."""
    goto_page(dash, server, "/tracker")
    assert dash.locator("text=📆 Activity >> visible=true").count() >= 1
    day_buttons = dash.locator(".st-key-calgrid button")
    assert day_buttons.count() >= 28
    # seeded events (applied/shortlisted) happened at seed time = today
    assert dash.locator("text=applied >> visible=true").count() >= 1
    assert dash.locator("text=Razorpay >> visible=true").count() >= 1
    # clicking another (event-free, non-today) day updates the panel IN PLACE
    from datetime import date
    day = "15" if date.today().day != 15 else "16"
    dash.locator(".st-key-calgrid button").filter(
        has_text=re.compile(rf"^{day}$")).first.click()
    dash.wait_for_timeout(1500)
    assert dash.locator("text=no activity >> visible=true").count() >= 1


def test_followup_queue_shows_aged_application(dash, server):
    goto_page(dash, server, "/tracker")
    assert dash.locator("text=Needs action >> visible=true").count() >= 1
    # the 9-day-old applied row must be listed (visible in THIS tab, not a hidden one)
    assert dash.locator("text=Razorpay >> visible=true").count() >= 1


def test_outreach_tab_draft_email(dash, server):
    goto_page(dash, server, "/outreach")
    dash.locator("summary", has_text="GenAI Engineer").first.click()
    dash.wait_for_timeout(800)
    # contact from posting shown on the card
    assert dash.locator("text=hr@fractal.ai >> visible=true").count() >= 1
    dash.locator("button", has_text="Draft email").first.click()
    dash.wait_for_timeout(1500)
    body = dash.locator("textarea").last.input_value()
    assert "Fractal" in body
    from jobscout.settings import load_configs
    _, profile = load_configs()
    assert profile["name"].split()[0] in body  # signed with the profile's name


def test_prospect_card_speculative_draft(dash, server):
    goto_page(dash, server, "/outreach")
    dash.get_by_text("Prospects", exact=True).first.click()
    dash.wait_for_timeout(1200)
    dash.locator("summary", has_text="Pixxel").first.click()
    dash.wait_for_timeout(800)
    dash.locator("button", has_text="Speculative draft").first.click()
    dash.wait_for_timeout(1500)
    body = dash.locator("textarea").last.input_value()
    assert "Pixxel" in body
    assert "don't see a specific opening" in body


def test_status_change_survives_reload(dash, server):
    conn = db_conn(server)
    conn.execute("UPDATE jobs SET status='shortlisted', status_updated_at=datetime('now') "
                 "WHERE title='LLM Platform Engineer'")
    conn.commit()
    conn.close()
    dash.reload()
    dash.wait_for_selector("text=Fresh matches", timeout=30000)
    dash.wait_for_timeout(1500)
    goto_page(dash, server, "/tracker")
    dash.locator("summary", has_text="All tracked jobs").click()
    dash.wait_for_timeout(800)
    assert dash.locator("text=LLM Platform Engineer >> visible=true").count() >= 1


def test_concurrent_refresh_does_not_lock_ui(dash, server):
    """Status clicks succeed while a pipeline hammers the same DB (WAL)."""
    script = f"""
import sys, time
sys.path.insert(0, {str(ROOT)!r})
from jobscout import db
from jobscout.models import Job
conn = db.connect({str(server['db_path'])!r})
for i in range(300):
    db.upsert(conn, Job(source='naukri', title=f'Load {{i}}', company=f'C{{i}}',
                        url=f'https://load/{{i}}', location='Pune'), 10, [])
"""
    proc = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-c", script])
    try:
        time.sleep(0.3)  # ensure the writer is running
        card_of(dash, "AI Infrastructure Engineer").locator(
            "button", has_text="Shortlist").first.click()
        dash.wait_for_timeout(2000)
        # no lock error surfaced and the write landed
        assert dash.locator("text=OperationalError").count() == 0
        assert dash.locator("text=database is locked").count() == 0
        assert job_status(server, "AI Infrastructure Engineer") == "shortlisted"
    finally:
        proc.wait(timeout=60)


# ── the Map page (WS-B) ─────────────────────────────────────────────
# The layout lives in the component's JavaScript, so these assert on what
# the browser actually drew: how many circles, where they ended up, and
# whether the picture is the same one after a rerun.

def _map_nodes(page):
    """[(slug, cx, cy, r), ...] for every node the map drew, sorted."""
    got = page.eval_on_selector_all(
        "circle.jsm-node",
        """els => els.map(e => [e.dataset.slug, +e.getAttribute('cx'),
                                +e.getAttribute('cy'), +e.getAttribute('r')])""")
    return sorted(got)


def test_map_renders_and_navigates(page, server):
    """/map draws one node per topic and a line for the alpha->beta edge;
    clicking a node opens that topic's page."""
    goto_page(page, server, "/map")
    page.wait_for_selector("circle.jsm-node", timeout=30000)
    assert page.locator("circle.jsm-node").count() == 2      # the two fixtures
    assert page.locator("line.jsm-edge").count() >= 1        # alpha -> beta
    assert page.locator("text=2 topics >> visible=true").count() >= 1
    page.locator("circle.jsm-node[data-slug='demo-alpha-topic']").click()
    page.wait_for_url(re.compile(r"topic=demo-alpha-topic"), timeout=30000)
    page.wait_for_selector("#jsr-art h1", timeout=30000)
    assert "topic=demo-alpha-topic" in page.url


def test_map_track_filter(page, server):
    """Dropping a track pill takes its nodes off the canvas. Both fixture
    topics are llm-infra, so deselecting it empties the map."""
    goto_page(page, server, "/map")
    page.wait_for_selector("circle.jsm-node", timeout=30000)
    assert page.locator("circle.jsm-node").count() == 2
    page.get_by_text("llm-infra", exact=True).first.click()
    page.wait_for_timeout(2500)
    assert page.locator("circle.jsm-node").count() < 2
    assert page.locator("text=No topics match these filters >> visible=true").count() >= 1
    # put it back so the page is usable again
    page.get_by_text("llm-infra", exact=True).first.click()
    page.wait_for_timeout(2500)
    assert page.locator("circle.jsm-node").count() == 2


def test_map_layout_is_deterministic(page, server):
    """Streamlit reruns the whole script on every interaction: the layout is
    seeded from the node set, so the same topics must land in the same place
    rather than jumping under the cursor."""
    goto_page(page, server, "/map")
    page.wait_for_selector("circle.jsm-node", timeout=30000)
    before = _map_nodes(page)
    assert len(before) == 2
    for _ in range(2):                       # off -> on -> off, two full reruns
        page.get_by_text("Unstudied only", exact=True).first.click()
        page.wait_for_timeout(2500)
    page.wait_for_selector("circle.jsm-node", timeout=30000)
    after = _map_nodes(page)
    assert [n[0] for n in after] == [n[0] for n in before]
    for (slug, x1, y1, _), (_, x2, y2, _) in zip(before, after):
        assert abs(x1 - x2) <= 1 and abs(y1 - y2) <= 1, f"{slug} moved"


def test_map_has_no_external_resources(page, server):
    """The dashboard runs offline and in Docker: the component may not pull
    a script, a stylesheet or a font from anywhere."""
    from jobscout import mapview
    blob = mapview._HTML + mapview._CSS + mapview._JS
    for needle in ("https://", "http://", "//cdn", "fetch(", "@import"):
        assert needle not in blob, f"component source references {needle}"
    goto_page(page, server, "/map")
    page.wait_for_selector("circle.jsm-node", timeout=30000)
    assert page.eval_on_selector_all(
        ".jsm-wrap [src], .jsm-wrap [href]", "els => els.length") == 0


def test_map_nodes_do_not_overlap(page, server):
    """Two circles on top of each other are two topics you cannot click."""
    import math

    import pytest
    goto_page(page, server, "/map")
    page.wait_for_selector("circle.jsm-node", timeout=30000)
    got = _map_nodes(page)
    if len(got) < 2:
        pytest.skip("need at least two topics to overlap")
    for i in range(len(got)):
        for j in range(i + 1, len(got)):
            (s1, x1, y1, r1), (s2, x2, y2, r2) = got[i], got[j]
            d = math.hypot(x1 - x2, y1 - y2)
            assert d >= r1 + r2, f"{s1} and {s2} overlap: {d:.1f} < {r1 + r2}"
