"""Writing pack tests.

Fixtures are synthetic (tests/unit/fixtures/pack) and copied into tmp_path;
nothing here reads the real study folder.
"""
import shutil
from pathlib import Path

import pytest

from jobscout import db, pack

FIXTURES = Path(__file__).parent / "fixtures" / "pack"

WIDGET = "dsa-widget-search"
PLAIN = "gizmo-plain-topic"
FRONT = "dsa-frontmatter-topic"

# two sentences that really live in the widget topic body, in this order
Q_FIRST = "keeps a running summary of everything\nbehind it"
Q_SECOND = "Resetting the running summary inside the loop"


def study(tmp_path, cards=True, diagrams=True, sessions=True) -> Path:
    """A synthetic study dir, optionally without the optional inputs."""
    root = tmp_path / "study"
    shutil.copytree(FIXTURES, root)
    if not cards:
        shutil.rmtree(root / "cards")
    if not diagrams:
        shutil.rmtree(root / "diagrams")
    if not sessions:
        (root / "session-notes.md").unlink()
    return root


def test_e1_highlights_in_article_order(tmp_path):
    """E1: both quotes and notes land in the pack, in article order even
    though they were inserted in reverse."""
    root = study(tmp_path)
    conn = db.init_db(tmp_path / "t.db")
    db.add_study_note(conn, WIDGET, Q_SECOND, note="second in the article")
    db.add_study_note(conn, WIDGET, Q_FIRST, note="first in the article")

    md = pack.build_pack(root, conn, WIDGET)

    assert "## Your highlights and notes" in md
    for text in ("running summary of everything behind it",
                 "Resetting the running summary inside the loop",
                 "first in the article", "second in the article"):
        assert text in md, text
    assert md.index("first in the article") < md.index("second in the article")
    assert pack.NO_NOTES not in md


def test_e1_unmatched_quote_sorts_last(tmp_path):
    """A highlight whose text no longer appears in the body is kept, last."""
    root = study(tmp_path)
    conn = db.init_db(tmp_path / "t.db")
    db.add_study_note(conn, WIDGET, "a sentence the routine deleted",
                      note="orphan note")
    db.add_study_note(conn, WIDGET, Q_FIRST, note="anchored note")

    md = pack.build_pack(root, conn, WIDGET)

    assert md.index("anchored note") < md.index("orphan note")


def test_e1_empty_note_renders_placeholder(tmp_path):
    root = study(tmp_path)
    conn = db.init_db(tmp_path / "t.db")
    db.add_study_note(conn, WIDGET, Q_FIRST)

    assert "(no note)" in pack.build_pack(root, conn, WIDGET)


def test_e2_bare_topic(tmp_path):
    """E2: no notes, no deck, no diagram still writes the body and says so."""
    root = study(tmp_path, cards=False, diagrams=False, sessions=False)
    conn = db.init_db(tmp_path / "t.db")

    md = pack.build_pack(root, conn, PLAIN)

    assert "# Gizmo plumbing" in md
    assert "placeholder subject with no deck" in md
    assert "## Your highlights and notes" in md
    assert pack.NO_NOTES in md
    assert "## Flashcards" not in md
    assert "## Diagram" not in md
    assert "## Session notes" not in md


def test_e2_deck_and_diagram_included_when_present(tmp_path):
    root = study(tmp_path)
    conn = db.init_db(tmp_path / "t.db")

    md = pack.build_pack(root, conn, WIDGET)

    assert "## Flashcards" in md
    assert "Q: What does the running summary buy you?" in md
    assert "## Diagram" in md
    assert f"diagrams/{WIDGET}.svg" in md


def test_e3_only_matching_session_section(tmp_path):
    """E3: the session section for another topic is left out."""
    root = study(tmp_path)
    conn = db.init_db(tmp_path / "t.db")

    md = pack.build_pack(root, conn, WIDGET)

    assert "## Session notes" in md
    # the heading drops the "DSA: " track prefix, as the real file does
    assert "2026-01-07 — Widget search" in md
    assert "Why is the widget scan linear?" in md
    assert "2026-01-09 — Gizmo plumbing" not in md
    assert "What is a gizmo made of?" not in md
    # the file's own preamble is not a session
    assert "Synthetic notes shaped like the real file" not in md


def test_e3_no_matching_section_skips_heading(tmp_path):
    root = study(tmp_path)
    conn = db.init_db(tmp_path / "t.db")

    md = pack.build_pack(root, conn, FRONT)

    assert "## Session notes" not in md


def test_frontmatter_stripped_and_track_taken_from_it(tmp_path):
    root = study(tmp_path)
    conn = db.init_db(tmp_path / "t.db")

    md = pack.build_pack(root, conn, FRONT)

    assert "---" not in md
    assert "updated: 2026-01-05" not in md
    assert "# Frontmatter sample topic" in md
    # frontmatter wins over the `dsa-` slug prefix
    assert f"track: llm-infra · slug: {FRONT} ·" in md


def test_session_needles_include_title_without_track_prefix(tmp_path):
    needles = pack.session_needles("dsa-widget-search", "DSA: widget search")
    assert "dsa-widget-search" in needles
    assert "dsa: widget search" in needles
    assert "widget search" in needles
    # a title with no track prefix contributes no extra needle
    assert pack.session_needles("gizmo-plain-topic", "Gizmo plumbing") == \
        ["gizmo-plain-topic", "gizmo plumbing"]


def test_track_derived_from_slug_prefix(tmp_path):
    assert pack.track_for("dsa-widget-search") == "dsa"
    assert pack.track_for("resume-auth") == "resume"
    assert pack.track_for("system-design-queues") == "system-design"
    assert pack.track_for("dotnet-async") == "backend"
    assert pack.track_for("sql-indexes") == "backend"
    assert pack.track_for("api-contracts") == "backend"
    assert pack.track_for("llm-serving") == "llm-infra"
    assert pack.track_for("rag-basics") == "llm-infra"
    assert pack.track_for("agentic-tools") == "llm-infra"
    assert pack.track_for("gizmo-plain-topic") == "unknown"


def test_title_falls_back_to_slug(tmp_path):
    assert pack.title_for("dsa-widget-search", "no heading here") == \
        "Dsa Widget Search"


def test_header_names_track_slug_and_privacy(tmp_path):
    root = study(tmp_path)
    conn = db.init_db(tmp_path / "t.db")

    md = pack.build_pack(root, conn, WIDGET)

    assert md.startswith("# DSA: widget search\n")
    assert f"track: dsa · slug: {WIDGET} · generated: " in md
    assert pack.REMINDER in md
    # the header prints the title, so the body's own H1 is not repeated
    assert md.count("# DSA: widget search") == 1
    assert md.splitlines()[6] == "## Concept"


def test_strip_leading_h1_leaves_bodies_without_one_alone(tmp_path):
    assert pack.strip_leading_h1("# Title\n\n## Concept\nx") == "## Concept\nx"
    assert pack.strip_leading_h1("## Concept\nx") == "## Concept\nx"


def test_write_pack_creates_packs_dir(tmp_path):
    root = study(tmp_path)
    conn = db.init_db(tmp_path / "t.db")

    path = pack.write_pack(root, conn, WIDGET)

    assert path == root / "packs" / f"{WIDGET}.md"
    assert path.read_text().startswith("# DSA: widget search")


def test_missing_topic_raises(tmp_path):
    root = study(tmp_path)
    conn = db.init_db(tmp_path / "t.db")

    with pytest.raises(FileNotFoundError):
        pack.build_pack(root, conn, "no-such-topic")


def test_cli_missing_topic_exits_1(tmp_path, monkeypatch, capsys):
    root = study(tmp_path)
    monkeypatch.setenv("JOBSCOUT_STUDY", str(root))
    db.init_db(tmp_path / "cli.db")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cli.db")

    assert pack.main(["no-such-topic"]) == 1
    assert "no topic file" in capsys.readouterr().err


def test_cli_writes_and_prints_path(tmp_path, monkeypatch, capsys):
    root = study(tmp_path)
    monkeypatch.setenv("JOBSCOUT_STUDY", str(root))
    db.init_db(tmp_path / "cli.db")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cli.db")

    assert pack.main([WIDGET]) == 0
    out = capsys.readouterr().out.strip()
    assert out == str(root / "packs" / f"{WIDGET}.md")
    assert (root / "packs" / f"{WIDGET}.md").exists()
