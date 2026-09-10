"""Playwright E2E against the seeded dashboard (see conftest.seed for data)."""
import math
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from conftest import PORT as E2E_PORT, ROOT, db_conn, goto_page, job_status

# the legibility gate runs a second dashboard of its own; keep it off the
# suite's port and off 8596, which is where the real-base measurement runs
DENSE_PORT = E2E_PORT - 1


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


def test_publish_toggle(page, server):
    """"Publish this version" writes the hash of the file you just read into
    study_publish, and unticking clears the row.

    The hash, not the slug, is the thing being stored: the exporter compares
    it with the file on disk and holds the topic back when the routine has
    deepened it since. Asserting the exact sha256 here is what makes that
    comparison meaningful rather than a formality.

    Deepening the file behind the page's back is the case that matters, so
    the fixture topic is rewritten mid-test and restored (text and mtime,
    because mtime is what "studied" is measured against) before the suite
    moves on.

    Only the technical half of the control is covered. The disabled resume
    variant needs a third fixture topic, and this suite has three assertions
    that count the fixture topics exactly (the checklist's "0/2 studied" and
    two map tests asserting two nodes), so adding one would break them.
    """
    import os

    from jobscout import publish

    topic_file = (Path(server["db_path"]).parent / "study" / "topics"
                  / "demo-alpha-topic.md")
    original, stat = topic_file.read_text(), topic_file.stat()
    goto_page(page, server, "/study?topic=demo-alpha-topic")
    page.wait_for_selector("#jsr-art h1", timeout=30000)
    assert page.locator("text=Publish this version >> visible=true").count() >= 1

    page.locator("div[data-testid='stCheckbox']",
                 has_text="Publish this version").first.click()
    page.wait_for_selector("text=This version is approved", timeout=15000)
    conn = db_conn(server)
    row = conn.execute("SELECT reviewed_at, content_hash FROM study_publish "
                       "WHERE slug=?", ("demo-alpha-topic",)).fetchone()
    assert row is not None and row["reviewed_at"]
    assert row["content_hash"] == publish.content_hash(original)

    try:
        # the routine deepens the topic overnight: the flag is still on, but
        # the approved version is no longer the one on disk
        topic_file.write_text(original + "\nDeepened after you reviewed it.\n")
        deepened = publish.content_hash(topic_file.read_text())
        goto_page(page, server, "/study?topic=demo-alpha-topic")
        page.wait_for_selector(
            "text=the published version differs from the current file",
            timeout=30000)
        assert conn.execute("SELECT content_hash FROM study_publish WHERE slug=?",
                            ("demo-alpha-topic",)).fetchone()["content_hash"] \
            != deepened
        page.locator("button",
                     has_text="Re-review and publish this version").first.click()
        page.wait_for_selector("text=This version is approved", timeout=15000)
        assert conn.execute("SELECT content_hash FROM study_publish WHERE slug=?",
                            ("demo-alpha-topic",)).fetchone()["content_hash"] \
            == deepened
    finally:
        topic_file.write_text(original)
        os.utime(topic_file, (stat.st_atime, stat.st_mtime))

    goto_page(page, server, "/study?topic=demo-alpha-topic")
    page.wait_for_selector("#jsr-art h1", timeout=30000)
    page.locator("div[data-testid='stCheckbox']",
                 has_text="Publish this version").first.click()
    page.wait_for_selector("text=Off by default", timeout=15000)
    assert conn.execute("SELECT slug FROM study_publish WHERE slug=?",
                        ("demo-alpha-topic",)).fetchone() is None
    conn.close()


def test_topic_page_concepts_and_prereqs(page, server):
    """The concept strip and the prerequisite strip, under Related.

    Chips are the topic's own `concepts` frontmatter, one per name. The
    prerequisite strip is the frontmatter list after your corrections, and
    the corrections are the point: the `x` writes a "remove" row that the
    nightly rewrite of the topic file cannot undo, the prerequisite stays on
    the page struck through so you can see what you turned off, and
    "restore" deletes the row again.

    The picker is asserted empty on alpha's page, which is the cycle guard
    doing its job rather than an accident of the fixture: beta already needs
    alpha, so offering beta would close a loop, and the two-topic fixture has
    nothing else to offer.

    The override table is left empty for the suites that follow.
    """
    goto_page(page, server, "/study?topic=demo-alpha-topic")
    page.wait_for_selector("div.topic-concepts", timeout=30000)
    chips = page.locator("div.topic-concepts a.chip")
    assert chips.count() == 2
    assert [chips.nth(i).inner_text() for i in range(2)] == \
        ["alpha concept", "shared concept"]
    assert chips.first.get_attribute("href") == "concepts?c=c%3Aalpha%20concept"

    # alpha declares no prerequisites, and the picker has nothing to offer:
    # beta needs alpha, so it would close a loop
    assert page.locator("text=Prerequisites: none declared").count() == 1
    picker = page.locator("div[data-testid='stSelectbox']",
                          has_text="add a prerequisite").first
    picker.locator("input").click()
    page.wait_for_timeout(600)
    assert page.locator("li[role='option']").count() == 0
    page.keyboard.press("Escape")

    # beta needs alpha, and the strip links at it
    goto_page(page, server, "/study?topic=demo-beta-topic")
    page.wait_for_selector("div.topic-prereqs", timeout=30000)
    assert page.locator("div.topic-prereqs").first.inner_text() == "Prerequisites:"
    assert page.locator("a.prereq-live[href='study?topic=demo-alpha-topic']"
                        ).count() == 1

    conn = db_conn(server)
    try:
        page.locator(
            ".st-key-prq_rm_demo-beta-topic_demo-alpha-topic button").first.click()
        page.wait_for_selector("a.prereq-gone", timeout=30000)
        row = conn.execute(
            "SELECT slug, prereq, action FROM study_prereq_overrides").fetchone()
        assert row is not None
        assert (row["slug"], row["prereq"], row["action"]) == \
            ("demo-beta-topic", "demo-alpha-topic", "remove")
        gone = page.locator("a.prereq-gone[href='study?topic=demo-alpha-topic']")
        assert gone.count() == 1
        assert "line-through" in (gone.get_attribute("style") or "")

        page.locator(
            ".st-key-prq_re_demo-beta-topic_demo-alpha-topic button").first.click()
        page.wait_for_selector("a.prereq-live[href='study?topic=demo-alpha-topic']",
                               timeout=30000)
        assert page.locator("a.prereq-gone").count() == 0
        assert conn.execute(
            "SELECT COUNT(*) c FROM study_prereq_overrides").fetchone()["c"] == 0
    finally:
        conn.execute("DELETE FROM study_prereq_overrides")
        conn.commit()
        conn.close()


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


def _learn_card_title(page):
    """The title on the worksheet's LEARN card. Scoped through the card's own
    markup rather than a page-wide text match, because both demo topics are
    also named in the checklist below."""
    return page.locator(
        "xpath=//div[contains(@class,'jcard-sub')][contains(.,'LEARN')]"
        "/following-sibling::div[contains(@class,'jcard-title')]"
    ).first.inner_text().strip()


def test_study_worksheet_orders_by_readiness(dash, server):
    """The worksheet picks the readiest topic, not the oldest: beta requires
    alpha in the fixtures, so alpha is handed over first and beta's checklist
    row says what it is waiting for. Ticking alpha promotes beta and the hint
    goes away."""
    goto_page(dash, server, "/study")
    assert "Picked by readiness" in dash.locator(
        "text=Picked by readiness >> visible=true").first.inner_text()
    assert _learn_card_title(dash) == "Demo Alpha Topic"
    beta = dash.locator("div[data-testid='stCheckbox']", has_text="Demo Beta Topic").first
    assert "after: Demo Alpha Topic" in beta.inner_text()

    dash.locator("div[data-testid='stCheckbox']", has_text="Demo Alpha Topic").first.click()
    dash.wait_for_timeout(1800)
    assert dash.locator("text=1/2 studied >> visible=true").count() >= 1
    assert _learn_card_title(dash) == "Demo Beta Topic"
    assert dash.locator("text=after: Demo Alpha Topic >> visible=true").count() == 0

    # untick to leave state clean for other tests
    dash.locator("div[data-testid='stCheckbox']", has_text="Demo Alpha Topic").first.click()
    dash.wait_for_timeout(1500)
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
    """[(slug, x0, y0, r), ...] for every node the map drew, sorted.

    `data-x0`/`data-y0` are the settled layout of record: solved once before
    the first paint and never rewritten. The live `cx`/`cy` drift a couple of
    pixels every frame, which is the whole point of the map, so they are no
    use for asking where a topic lives.
    """
    got = page.eval_on_selector_all(
        "circle.jsm-node",
        """els => els.map(e => [e.dataset.slug, +e.dataset.x0,
                                +e.dataset.y0, +e.getAttribute('r')])""")
    return sorted(got)


# ── the topic-page overview diagram (WS-C2) ─────────────────────────

def test_topic_page_diagram(page, server):
    """A topic with study/diagrams/<slug>.svg shows it above the deck, as the
    first thing on the page after the Related strip; a topic without one shows
    no image at all rather than an empty frame."""
    goto_page(page, server, "/study?topic=demo-alpha-topic")
    page.wait_for_selector("text=Overview", timeout=30000)

    image = page.locator("[data-testid='stImage'] img, img[src*='.svg']").first
    image.wait_for(timeout=15000)
    assert image.is_visible()

    # the picture comes before the deck, not after it
    deck = page.get_by_text("Flashcards", exact=False).first
    assert image.bounding_box()["y"] < deck.bounding_box()["y"]

    # the editable scene has not been written for this fixture, so the page
    # offers the picture without claiming there is a source next to it
    assert page.locator("text=Editable source").count() == 0

    # beta has no svg, so its page carries no diagram at all
    goto_page(page, server, "/study?topic=demo-beta-topic")
    page.wait_for_selector("#jsr-art h1", timeout=30000)
    assert page.locator("text=Overview").count() == 0
    assert page.locator("[data-testid='stImage']").count() == 0


def test_concepts_page(page, server):
    """/concepts renders the generated sheet: the fixture's shared concept is
    listed once under "Shared concepts" however many topics name it, `?c=`
    loads without complaint, and a topic heading is a link to that topic."""
    goto_page(page, server, "/concepts")
    page.wait_for_selector("text=Shared concepts", timeout=30000)
    body = page.locator("section.main, [data-testid='stMain']").first.inner_text()
    assert "alpha concept" in body and "beta concept" in body
    # four concepts across the two fixture topics, no definitional card for
    # any of them (alpha's deck asks what the DECK proves, not what a
    # concept is), so every bullet is a gap
    assert body.count("(no card yet)") >= 4

    # both fixture topics name it, and the shared section names it once
    shared = body.split("Shared concepts", 1)[1].split("Study order", 1)[0]
    assert shared.lower().count("shared concept") == 1

    # the anchored form of the concept, and the raw name, both just load
    for value in ("concept-shar", "shared-concept"):
        goto_page(page, server, f"/concepts?c={value}")
        page.wait_for_selector("text=Shared concepts", timeout=30000)
        assert page.locator("text=Traceback >> visible=true").count() == 0
        assert page.locator("[data-testid='stException']").count() == 0
    # the last of those was the live anchor: the script found it and marked
    # the row, which is the only observable proof that it ran at all
    goto_page(page, server, "/concepts?c=concept-shar")
    # an empty anchor has no box of its own, so wait for it in the DOM
    page.wait_for_selector("#concept-shar", state="attached", timeout=30000)
    page.wait_for_timeout(1200)
    assert page.evaluate(
        "document.getElementById('concept-shar').parentElement.style.background")

    # the sheet's topic headings are topic-page links
    goto_page(page, server, "/concepts")
    page.get_by_role("link", name=re.compile("Demo Alpha Topic")).first.click()
    page.wait_for_url(re.compile(r"topic=demo-alpha-topic"), timeout=30000)
    page.wait_for_selector("#jsr-art h1", timeout=30000)


def test_map_renders_and_navigates(page, server):
    """/map draws one node per topic and a line for the alpha->beta edge;
    clicking a node opens that topic's page."""
    goto_page(page, server, "/map")
    page.wait_for_selector("circle.jsm-node", timeout=30000)
    assert page.locator("circle.jsm-node").count() == 2      # the two fixtures
    assert page.locator("line.jsm-edge").count() >= 1        # alpha -> beta
    assert page.locator("text=2 topics >> visible=true").count() >= 1
    # Nothing on this canvas holds still, and Playwright's built-in click
    # refuses a target whose box changed since the last frame - a bar no live
    # simulation can clear. Hovering first is what the component itself asks
    # for: the node under the pointer stops drifting, and the click then
    # lands as a real one, hit-testing included, rather than as force=True.
    box = page.locator("circle.jsm-node[data-slug='demo-alpha-topic']").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(400)
    box = page.locator("circle.jsm-node[data-slug='demo-alpha-topic']").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
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
    """Streamlit reruns the whole script on every interaction: the settled
    layout is seeded from the node set, so the same topics must land in the
    same place rather than jumping under the cursor.

    The live coordinates are the opposite promise. They are supposed to move,
    every frame, for as long as the page is open - unless the reader has told
    the OS they would rather things held still.
    """
    live = """els => els.map(e => [e.dataset.slug, +e.getAttribute('cx')])"""
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

    # the simulation is still running: two samples 700ms apart differ. The
    # drift is only a few pixels wide and slow, so the bar is deliberately
    # low - "not frozen" is the claim, not "visibly swinging".
    a = dict(page.eval_on_selector_all("circle.jsm-node", live))
    page.wait_for_timeout(700)
    b = dict(page.eval_on_selector_all("circle.jsm-node", live))
    assert max(abs(b[s] - a[s]) for s in a) > 0.05, "the map stopped moving"

    # ...and it is not running for a reader who asked for less motion
    page.emulate_media(reduced_motion="reduce")
    goto_page(page, server, "/map")
    page.wait_for_selector("circle.jsm-node", timeout=30000)
    page.wait_for_timeout(2500)
    a = dict(page.eval_on_selector_all("circle.jsm-node", live))
    page.wait_for_timeout(700)
    assert dict(page.eval_on_selector_all("circle.jsm-node", live)) == a, \
        "reduced motion should paint once and stop"


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
    """Two circles on top of each other are two topics you cannot click.

    Judged on the settled layout, which is where the separation pass runs and
    what the drawing is framed around; the live drift is a couple of pixels
    of breathing on top of a gap the solver already opened.
    """
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


def test_map_double_click_unpins(page, server):
    """A drag pins a topic where you let go; a double-click lets it go again.

    What "pinned" is observable as is the `jsm-pinned` class plus the fact
    that it survives a rerun: the pin is emitted with `setStateValue("pins",
    ...)`, lands in session state, and comes back down through `data.pins`
    on the fresh mount, so an unrelated filter toggle must not shake it off.

    It is deliberately *not* asserted as stillness. Pinning takes the node out
    of the force integration, but the per-node breathing wander is a
    render-time offset that runs on every node regardless - a pinned circle
    still wobbles its two or three pixels (measured: ~3.3px of `cx` span over
    three seconds, against ~3.9px unpinned).

    The double-click also has to not open the topic. A pinned node delays its
    single click by 220ms precisely so a second one can cancel it, and this is
    the test that the delay actually catches it.
    """
    def is_pinned():
        return page.eval_on_selector(
            "circle.jsm-node[data-slug='demo-alpha-topic']",
            "e => e.closest('g.jsm-item').classList.contains('jsm-pinned')")

    def centre():
        b = page.locator("circle.jsm-node[data-slug='demo-alpha-topic']").bounding_box()
        return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2

    def cx_span(samples=12, gap=120):
        """How far `cx` travels over ~1.4s. The wander is a slow Lissajous, so
        one pair of samples can straddle a turning point; a window cannot."""
        got = []
        for _ in range(samples):
            got.append(page.eval_on_selector(
                "circle.jsm-node[data-slug='demo-alpha-topic']",
                "e => +e.getAttribute('cx')"))
            page.wait_for_timeout(gap)
        return max(got) - min(got)

    goto_page(page, server, "/map")
    page.wait_for_selector("circle.jsm-node", timeout=30000)
    svg = page.locator("svg.jsm-svg").bounding_box()
    # somewhere on the canvas that is not a node: parking the pointer here
    # releases the hover freeze, which is a different thing from a pin and
    # would otherwise answer the drift question below for the wrong reason
    away = (svg["x"] + 5, svg["y"] + svg["height"] - 5)
    page.mouse.move(*away)
    page.wait_for_timeout(1200)
    assert not is_pinned()

    # Hover first, then re-read the box: the node stops drifting under the
    # pointer, so the coordinates the drag starts from are still true by the
    # time it presses. Same reason as test_map_renders_and_navigates.
    page.mouse.move(*centre())
    page.wait_for_timeout(400)
    sx, sy = centre()
    tx = min(max(sx + 90, svg["x"] + 60), svg["x"] + svg["width"] - 60)
    ty = min(max(sy + 70, svg["y"] + 60), svg["y"] + svg["height"] - 60)
    assert abs(tx - sx) + abs(ty - sy) > 40, "drag too short to count as one"
    page.mouse.down()
    for i in range(1, 11):                   # in steps, so onMove really runs
        page.mouse.move(sx + (tx - sx) * i / 10, sy + (ty - sy) * i / 10)
        page.wait_for_timeout(20)
    page.mouse.up()
    page.wait_for_timeout(2500)              # setStateValue -> rerun -> remount
    assert is_pinned(), "letting go of a drag should pin the topic"

    # and the pin is in session state, not just in the class list: two more
    # full reruns from an unrelated control, and it is still pinned
    for _ in range(2):
        page.get_by_text("Unstudied only", exact=True).first.click()
        page.wait_for_timeout(2500)
    assert is_pinned(), "the pin did not survive a rerun"

    was = page.url
    page.mouse.move(*centre())               # freeze the drift, then aim
    page.wait_for_timeout(400)
    page.mouse.dblclick(*centre())
    page.wait_for_timeout(2500)
    assert not is_pinned(), "a double-click should unpin"
    assert page.url == was, f"the double-click navigated to {page.url}"
    assert "topic=" not in page.url

    # let go of the hover freeze and it breathes again
    page.mouse.move(*away)
    page.wait_for_timeout(1200)
    assert cx_span() > 0.3, "an unpinned node should drift again"

    # pins live in session state for as long as the browser session does, so
    # hand the next test a fresh one rather than whatever this one left
    page.reload()
    page.wait_for_selector("circle.jsm-node", timeout=30000)


# ── WS-B3: concept mode on the map ──────────────────────────────────
# The map grew a second reading: every concept as a satellite of the topic
# that teaches it, an idea two topics share sitting between them, and the
# prerequisite DAG drawn with heads on it. The promise that makes it a mode
# rather than a different page is that the topics do not move.

def _map_mode(page, name):
    """Click one segment of the Topics/Concepts control and let it remount.

    Not `get_by_text`: "Concepts" is also a page in the sidebar nav, and the
    map has to be switched by the control on the map.
    """
    page.locator("div[data-testid='stButtonGroup'] button",
                 has_text=name).first.click()
    page.wait_for_timeout(3000)
    page.wait_for_selector("circle.jsm-node", timeout=30000)


def _labels(page):
    """[(text, x, y, w, h), ...] for every label, in rendered pixels, with
    the halo taken back off.

    The halo is a 3-unit stroke painted under the glyphs so a name stays
    readable where it crosses an edge, and `getBoundingClientRect` counts it.
    Two labels whose haloes touch are not two labels you cannot read, so what
    is measured here is the ink.
    """
    return page.eval_on_selector_all(".jsm-label", """els => els.map(e => {
      const b = e.getBoundingClientRect()
      const m = e.getScreenCTM()
      const sw = parseFloat(getComputedStyle(e).strokeWidth || 0) * (m ? m.a : 1)
      return [e.textContent, b.x + sw / 2, b.y + sw / 2,
              Math.max(0, b.width - sw), Math.max(0, b.height - sw)]
    })""")


def _label_overlaps(boxes, thresh):
    """Every pair of label boxes biting more than `thresh` px into each other.

    The bar is stated as a count rather than as a worst case: one pair at
    3px is a blemish, four hundred of them is a map you cannot read, and
    only the count tells those apart.
    """
    out = []
    for i in range(len(boxes)):
        _, x1, y1, w1, h1 = boxes[i]
        for j in range(i + 1, len(boxes)):
            _, x2, y2, w2, h2 = boxes[j]
            ox = min(x1 + w1, x2 + w2) - max(x1, x2)
            oy = min(y1 + h1, y2 + h2) - max(y1, y2)
            if ox > 0 and oy > 0 and min(ox, oy) > thresh:
                out.append((boxes[i][0], boxes[j][0], round(min(ox, oy), 2)))
    return out


def _worst_label_overlap(boxes):
    """The deepest any two label boxes bite into each other, and who."""
    worst, who = 0.0, None
    for i in range(len(boxes)):
        _, x1, y1, w1, h1 = boxes[i]
        for j in range(i + 1, len(boxes)):
            _, x2, y2, w2, h2 = boxes[j]
            ox = min(x1 + w1, x2 + w2) - max(x1, x2)
            oy = min(y1 + h1, y2 + h2) - max(y1, y2)
            if ox > 0 and oy > 0 and min(ox, oy) > worst:
                worst, who = min(ox, oy), (boxes[i][0], boxes[j][0])
    return worst, who


def _hub_distance_ratios(before, after):
    """distance(after) / distance(before) for every pair of hubs.

    The B3a promise is that the hub layout is the *same picture* in both
    modes, which is a statement about the relative geometry rather than about
    absolute coordinates: a mode that placed the identical drawing a uniform
    scale and a translation away would still be the same map, and the reader
    would still find the place they had learned. So what is checked is the
    shape - every pairwise distance stretched by the same factor - not the
    numbers. (Concept mode as built does not scale at all, so these ratios
    come out at 1; the test is written to the criterion, not to the number,
    so that a later decision to zoom the mode out is not a test failure.)
    """
    out = []
    slugs = sorted(before)
    for i, a in enumerate(slugs):
        for b in slugs[i + 1:]:
            d0 = math.dist(before[a], before[b])
            d1 = math.dist(after[a], after[b])
            if d0 > 1e-6:
                out.append(d1 / d0)
    return out


def test_map_concept_mode_keeps_hub_positions(page, server):
    """B3a. Switching to concepts adds satellites; it does not redraw the
    map. The hubs are settled from the topic-only node set and the topic-only
    seed, before a satellite exists, so the picture the topics make has to be
    the same one in both modes - identical up to a uniform scale and a
    translation - or the mode reads as a different map and the reader loses
    the place they had learned."""
    goto_page(page, server, "/map")
    page.wait_for_selector("circle.jsm-node", timeout=30000)
    before = {s: (x, y) for s, x, y, _ in _map_nodes(page)}
    assert len(before) == 2

    _map_mode(page, "Concepts")
    after = {s: (x, y) for s, x, y, _ in _map_nodes(page)
             if not s.startswith("c:")}
    assert set(after) == set(before), "the topics themselves changed"
    ratios = _hub_distance_ratios(before, after)
    assert ratios and max(ratios) / min(ratios) <= 1.02, \
        f"the hub layout is not the same shape: ratios {ratios}"

    # ...and back again, so the mode is a view and not a one-way door
    _map_mode(page, "Topics")
    back = {s: (x, y) for s, x, y, _ in _map_nodes(page)}
    for slug, (x1, y1) in before.items():
        x2, y2 = back[slug]
        assert abs(x1 - x2) <= 1 and abs(y1 - y2) <= 1, f"{slug} moved back"


def test_map_concept_mode_nodes_and_arrows(page, server):
    """B3b. One satellite per canonical concept - the fixture's two topics
    name four concepts between them and one of those is the same idea twice,
    so three - and a prerequisite arrow from alpha to beta even though that
    pair is *also* related, which is the case the old one-line-per-pair
    payload silently dropped."""
    goto_page(page, server, "/map")
    page.wait_for_selector("circle.jsm-node", timeout=30000)
    _map_mode(page, "Concepts")

    sats = page.locator("circle.jsm-node[data-kind='concept']")
    assert sats.count() == 3, "one node per canonical concept"
    ids = sorted(page.eval_on_selector_all(
        "circle.jsm-node[data-kind='concept']",
        "els => els.map(e => e.dataset.slug)"))
    assert all(i.startswith("c:") for i in ids), ids
    assert page.locator("circle.jsm-node[data-kind='topic']").count() == 2

    pair = "[data-source='demo-alpha-topic'][data-target='demo-beta-topic']"
    arrow = page.locator(f"line.jsm-prereq{pair}")
    assert arrow.count() == 1, "the prerequisite arrow is missing"
    assert "url(#jsm-arrow" in (arrow.first.get_attribute("marker-end") or "")
    # the same pair still carries its related line: two facts, two lines
    assert page.locator("line.jsm-related").count() >= 1
    assert page.locator(f"line.jsm-edge{pair}").count() >= 2

    # the shared concept hangs off both topics
    shared = page.eval_on_selector_all(
        "line.jsm-concept",
        "els => els.map(e => [e.dataset.source, e.dataset.target])")
    targets = {}
    for src, tgt in shared:
        targets.setdefault(tgt, set()).add(src)
    assert any(len(v) == 2 for v in targets.values()), targets

    # clicking a concept opens its line on the sheet, not a topic page
    box = sats.first.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(400)
    box = sats.first.bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_url(re.compile(r"/concepts\?c="), timeout=30000)
    assert "topic=" not in page.url


# The legibility gate. Everything above this line is checked against the two
# fixture topics, which prove correctness and prove nothing about whether a
# real study base drawn this way is readable - B3 passed a gate built on a
# synthetic 75-node fixture and then arrived on the real base with 418
# overlapping label pairs on it.
#
# So the fixture is built to the real base's shape rather than to a round
# number: fourteen topics, twelve of them naming eight to ten concepts and
# two (the resume drills) naming none, a hundred and four canonical concepts
# of which ten are shared - nine by two topics and one by three - forty-two
# related edges, ten prerequisites, and names from eight characters up to
# the twenty-eight a label is cut to. It is built here rather than in
# conftest on purpose: every other test in this file counts the two demo
# topics, and a shared fixture this size would rewrite most of them.
_DENSE_ADJ = ["hot", "cold", "keyed", "layered", "deferred", "federated",
              "monotonic", "hierarchical", "unsynchronized", "canonical",
              "elastic", "buffered", "granular", "immutable"]
_DENSE_NOUN = ["page", "quorum", "gateway", "snapshot", "partition",
               "throttle", "checkpoint", "reconciliation"]
# Every name is two tokens out of a product, so no two of them normalise to
# the same concept and none is a near-duplicate of another (see
# jobscout.concepts.near_duplicate). "hot page" is 8 characters and
# "unsynchronized reconciliation" is 29, which the map cuts to 28.
_DENSE_NAMES = [f"{a} {b}" for a in _DENSE_ADJ for b in _DENSE_NOUN]
_DENSE_TRACKS = ["dsa", "backend", "system-design", "llm-infra"]
# the real base's depth range, topic by topic - two of them have never asked
# you anything and draw at the minimum radius
_DENSE_QS = [4, 4, 4, 4, 4, 4, 5, 7, 7, 9, 9, 10, 0, 0]
# how many concepts each topic names; the last two name none at all
_DENSE_COUNT = [8, 9, 9, 9, 10, 10, 10, 10, 10, 10, 10, 10, 0, 0]
# ten prerequisites, several of them on pairs that are also `related`, which
# is the shape the real base has
_DENSE_PREREQS = {2: [1], 3: [2], 4: [2], 6: [5], 7: [5], 9: [8], 10: [9],
                  12: [11], 13: [1, 4]}


def _dense_concepts():
    """{topic number: [concept name, ...]} at the real base's density.

    Ten names are shared - nine of them by a pair of topics and one by three
    - and the other ninety-four are named by exactly one topic, which is the
    hubs-per-concept histogram the real base has: {1: 94, 2: 9, 3: 1}.
    """
    shared, solo = _DENSE_NAMES[:10], _DENSE_NAMES[10:]
    out = {t: [] for t in range(1, 15)}
    for i in range(9):                       # (1,2), (2,3), ... (9,10)
        out[i + 1].append(shared[i])
        out[i + 2].append(shared[i])
    for t in (10, 11, 12):                   # the one idea three topics share
        out[t].append(shared[9])
    nxt = 0
    for t in range(1, 15):
        while len(out[t]) < _DENSE_COUNT[t - 1]:
            out[t].append(solo[nxt])
            nxt += 1
    assert nxt == 94, nxt
    return out


def _dense_study(root: Path):
    """14 topics, 104 canonical concepts, 118 nodes: the real base's shape."""
    study = root / "study"
    (study / "topics").mkdir(parents=True)
    (study / "STUDY.md").write_text("# Study Base\n\ndense fixture\n")
    names = _dense_concepts()
    for t in range(1, 15):
        slug = f"dense-topic-{t:02d}"
        related = [f"dense-topic-{(t + d - 1) % 14 + 1:02d}" for d in (1, 2, 4)]
        prereqs = [f"dense-topic-{p:02d}" for p in _DENSE_PREREQS.get(t, [])]
        questions = _DENSE_QS[t - 1]
        body = "\n".join(
            f"**Q{i}. What does dense topic {t:02d} ask you at step {i}?**\n\n"
            f"Answer {i} for topic {t:02d}.\n" for i in range(1, questions + 1))
        # a title long enough to be cut to the 28 characters a label gets,
        # which is where every one of the real base's topic names lands
        (study / "topics" / f"{slug}.md").write_text(
            f"---\ntrack: {_DENSE_TRACKS[(t - 1) % 4]}\ntags: [dense]\n"
            f"related: {related}\n"
            f"concepts: {names[t]}\nprereqs: {prereqs}\n"
            f"created: 2026-01-01\nupdated: 2026-01-02\n---\n"
            f"# Dense topic {t:02d}: partitioning, quorums and replay\n\n"
            f"## Q&A\n\n{body}\n")
    return study


def _dense_server(root: Path, port: int):
    """A second dashboard, on its own port, against the dense study folder.

    Started the way conftest starts the first one - same interpreter, same
    headless flags, same wait-for-the-socket loop - because the thing under
    test is the real page, not a harness that resembles it.
    """
    from jobscout import db
    db_path = root / "dense.db"
    db.init_db(db_path).close()
    briefs = root / "briefs"
    briefs.mkdir()
    (briefs / "TODAY.md").write_text("# Daily brief - dense\n")
    env = dict(os.environ, JOBSCOUT_DB=str(db_path),
               JOBSCOUT_DISABLE_REFRESH="1", JOBSCOUT_NO_LLM="1",
               JOBSCOUT_BRIEFS=str(briefs),
               JOBSCOUT_STUDY=str(_dense_study(root)))
    proc = subprocess.Popen(
        [str(ROOT / ".venv/bin/python"), "-m", "streamlit", "run",
         str(ROOT / "dashboard.py"), "--server.port", str(port),
         "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                break
        except OSError:
            time.sleep(0.5)
    else:
        proc.kill()
        raise RuntimeError("the dense dashboard did not start")
    return proc, f"http://localhost:{port}"


def test_map_concept_mode_is_legible(page, tmp_path):
    """B3c, and the whole reason this mode was allowed to be cancelled.

    Correctness was never the risk here - a hundred and eighteen circles and
    a hundred and sixty-nine lines is easy to draw and easy to draw
    unreadably. Four things are measured on a fixture built to the real
    base's density, and if any of them had failed the mode would have
    shipped behind a flag:

      * no two names sit on top of each other. Dodging circles is not enough
        once labels outnumber circles five to one, and neither is dodging
        labels: what has to be separated is the label *boxes*, in the layout,
        before a seat is ever chosen.
      * every name is still big enough to read. The viewBox is fitted to the
        drawing, so a bigger graph is a more zoomed-out one, and an 11px
        label on a map this size would arrive on screen at four. The font is
        specified in layout units scaled by however far the drawing shrank.
      * the frame loop still keeps time.
      * the canvas is the size the drawing needs, and the drawing is not
        floating in two bands of empty inside it.
    """
    proc, url = _dense_server(tmp_path, DENSE_PORT)
    try:
        page.goto(url + "/map")
        page.wait_for_timeout(2500)
        page.wait_for_selector("circle.jsm-node", timeout=30000)
        topic_mode = {s: (x, y) for s, x, y, _ in _map_nodes(page)}
        _map_mode(page, "Concepts")
        page.wait_for_timeout(2500)

        # B3a with enough hubs for it to mean something: ninety-one pairwise
        # distances, all stretched by the same factor
        concept_mode = {s: (x, y) for s, x, y, _ in _map_nodes(page)
                        if not s.startswith("c:")}
        ratios = _hub_distance_ratios(topic_mode, concept_mode)
        assert len(ratios) == 91, len(ratios)
        assert max(ratios) / min(ratios) <= 1.02, \
            f"the hub layout changed shape between modes: " \
            f"{min(ratios):.4f}..{max(ratios):.4f}"

        sats = page.locator("circle.jsm-node[data-kind='concept']").count()
        hubs = page.locator("circle.jsm-node[data-kind='topic']").count()
        assert hubs == 14, hubs
        assert sats == 104, f"104 canonical concepts, 10 of them shared: {sats}"
        assert page.locator("line.jsm-prereq").count() == 10

        boxes = _labels(page)
        assert len(boxes) == hubs + sats
        heights = sorted(b[4] for b in boxes)
        smallest = [b for b in boxes if b[4] < 9]
        assert not smallest, \
            f"unreadable labels (min {heights[0]:.2f}px): {smallest[:3]}"

        worst, who = _worst_label_overlap(boxes)
        over = _label_overlaps(boxes, 2.0)
        assert not over, \
            f"{len(over)} label pairs overlap by more than 2px: {over[:3]}"
        assert worst <= 2.0, f"labels overlap by {worst:.2f}px: {who}"

        # the frame loop, sampled from the page's own requestAnimationFrame
        # over three seconds. 20ms is 50fps; a 60Hz browser sits at 16.7.
        gap = page.evaluate("""() => new Promise(res => {
          const ts = []
          const t0 = performance.now()
          function tick(now) {
            ts.push(now)
            if (now - t0 < 3000) requestAnimationFrame(tick)
            else res((ts[ts.length - 1] - ts[0]) / (ts.length - 1))
          }
          requestAnimationFrame(tick)
        })""")
        assert gap < 20, f"the map runs at {gap:.1f}ms a frame"

        # the canvas grows with the crowd - 560px at fourteen nodes, up to
        # 900 - and is then the size the drawing actually needs, so a wide
        # graph is not two bands of empty with a map between them
        shape = page.eval_on_selector("svg.jsm-svg", """e => {
          const b = e.getBoundingClientRect()
          const v = e.viewBox.baseVal
          const s = Math.min(b.width / v.width, b.height / v.height)
          return [b.width, b.height, e.getAttribute("viewBox"),
                  s * v.height, s * v.width]
        }""")
        assert 560 < shape[1] <= 900, f"the canvas did not grow: {shape}"
        # on both axes: a drawing that grew into a tall ribbon wastes the
        # width just as surely as a wide one wastes the height
        assert shape[1] - shape[3] < 24, f"the drawing is letterboxed: {shape}"
        assert shape[0] - shape[4] < 24, f"the drawing is letterboxed: {shape}"

        print(f"\nB3c on 14 topics + {sats} concepts: label height "
              f"{heights[0]:.2f}..{heights[-1]:.2f}px, worst label overlap "
              f"{worst:.2f}px, frame interval {gap:.2f}ms, canvas "
              f"{shape[0]:.0f}x{shape[1]:.0f} viewBox {shape[2]}")
        # the canvas itself, not the page: Streamlit scrolls its main column
        # inside a container, so a full-page shot stops at the fold
        page.locator("div.jsm-wrap").first.screenshot(
            path=str(ROOT / "tests/e2e/.map-concepts.png"))
    finally:
        proc.terminate()
        proc.wait(timeout=10)
