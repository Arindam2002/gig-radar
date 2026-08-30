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


def test_topic_link_opens_focus_panel(page, server):
    """?topic=<slug> links (rewritten from file links) open the study focus
    panel on any tab (regression: raw file links were dead)."""
    page.goto(server["url"] + "/?topic=dotnet-async-concurrency")
    page.wait_for_selector("text=Study focus", timeout=30000)
    assert page.locator("text=async/await internals >> visible=true").count() >= 1


def test_brief_actions_apply_from_today_page(page, server):
    """Brief picks carry inline status actions - mark applied without hunting
    the job down in Fresh matches."""
    goto_page(page, server, "/")
    # DB-matched pick has action buttons; unknown external link has none
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
    # clicking another day updates the panel IN PLACE (websocket rerun)
    dash.locator(".st-key-calgrid button").filter(has_text=re.compile(r"^1$")).first.click()
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
