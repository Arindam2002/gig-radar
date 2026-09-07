"""The study link graph, against a synthetic four-topic study folder.

The fixture folder under fixtures/study_graph is copied into tmp_path for
every test: `repair` writes files, and the committed fixture must stay the
shape the assertions describe. Nothing here reads the real study folder
except the last test, which only reports.
"""
import os
import shutil
import sqlite3
import time
from datetime import date
from pathlib import Path

import pytest

from jobscout import db, graph, settings

FIXTURES = Path(__file__).parent / "fixtures" / "study_graph"
DAY = 86400


@pytest.fixture()
def study(tmp_path):
    """A writable copy of the fixture study folder, with every topic aged a
    day so "studied now" is unambiguously newer than the file."""
    dst = tmp_path / "study"
    shutil.copytree(FIXTURES, dst)
    old = time.time() - DAY
    for f in (dst / "topics").glob("*.md"):
        os.utime(f, (old, old))
    return dst


@pytest.fixture()
def conn(tmp_path):
    c = db.init_db(tmp_path / "t.db")
    yield c
    c.close()


def edge_set(g, kind=None):
    return {(e["source"], e["target"]) for e in g["edges"]
            if kind is None or e["kind"] == kind}


# ── A1: the node/edge contract ──────────────────────────────────────

def test_nodes_carry_title_track_tags_and_words(study, conn):
    g = graph.build(study, conn)
    by_slug = {n["slug"]: n for n in g["nodes"]}
    assert set(by_slug) == {"graph-alpha", "graph-beta", "graph-gamma",
                            "resume-graph-drill"}
    assert by_slug["graph-alpha"]["title"] == "Alpha, the first fixture topic"
    assert by_slug["graph-alpha"]["track"] == "backend"          # frontmatter
    assert by_slug["graph-alpha"]["tags"] == ["alpha", "fixture"]
    assert by_slug["graph-beta"]["track"] == "llm-infra"
    assert by_slug["graph-gamma"]["track"] == "unknown"          # no prefix match
    assert by_slug["graph-gamma"]["tags"] == []
    assert by_slug["resume-graph-drill"]["track"] == "resume"    # frontmatter + prefix
    # words counts the body, not the YAML: the frontmatter keys are not prose
    assert by_slug["graph-beta"]["words"] > 10
    assert "track" not in graph.split_frontmatter(
        (study / "topics" / "graph-beta.md").read_text())[1]


def test_edges_come_from_related_and_inline_links(study, conn):
    g = graph.build(study, conn)
    assert edge_set(g) == {
        ("graph-alpha", "graph-beta"),    # frontmatter, and an inline link too
        ("graph-beta", "graph-alpha"),
        ("graph-beta", "graph-gamma"),
        ("graph-gamma", "graph-alpha"),   # inline link only
    }
    assert edge_set(g, "related") == {
        ("graph-alpha", "graph-beta"),
        ("graph-beta", "graph-alpha"),
        ("graph-beta", "graph-gamma"),
    }
    assert edge_set(g, "link") == {("graph-gamma", "graph-alpha")}


def test_duplicate_pair_keeps_the_related_kind(study, conn):
    """Alpha declares beta AND links it in prose: one edge, the deliberate
    kind. Two edges here would double every line on the map."""
    g = graph.build(study, conn)
    pair = [e for e in g["edges"]
            if (e["source"], e["target"]) == ("graph-alpha", "graph-beta")]
    assert len(pair) == 1 and pair[0]["kind"] == "related"


def test_self_links_and_non_topic_links_are_not_edges(study, conn):
    g = graph.build(study, conn)
    assert ("graph-gamma", "graph-gamma") not in edge_set(g)
    assert not [e for e in g["edges"] if e["target"] == "session-notes"]


def test_missing_related_target_is_a_warning_not_an_edge(study, conn):
    g = graph.build(study, conn)
    assert "graph-ghost" not in {e["target"] for e in g["edges"]}
    assert "graph-alpha: related 'graph-ghost' has no file" in g["warnings"]


def test_warnings_flag_missing_frontmatter_and_thin_linking(study, conn):
    g = graph.build(study, conn)
    joined = "\n".join(g["warnings"])
    assert "graph-gamma: no frontmatter block" in g["warnings"]
    assert "graph-alpha: 1 related topic(s)" in joined     # ghost does not count
    assert "graph-gamma: 0 related topic(s)" in joined
    # beta has its two, and a resume drill is never asked for any
    assert "graph-beta:" not in joined
    assert "resume-graph-drill" not in joined


def test_studied_and_notes_come_from_the_db(study, conn):
    db.set_study_done(conn, "graph-beta", True)
    db.add_study_note(conn, "graph-beta", "Beta declares two resolvable")
    db.add_study_note(conn, "graph-alpha", "Alpha is a synthetic topic")
    g = graph.build(study, conn)
    by_slug = {n["slug"]: n for n in g["nodes"]}
    assert by_slug["graph-beta"]["studied"] is True
    assert by_slug["graph-beta"]["notes"] == 1
    assert by_slug["graph-alpha"]["studied"] is False
    assert by_slug["graph-alpha"]["notes"] == 1
    assert by_slug["graph-gamma"]["notes"] == 0


def test_a_topic_rewritten_after_you_ticked_it_is_not_studied(study, conn):
    db.set_study_done(conn, "graph-beta", True)
    f = study / "topics" / "graph-beta.md"
    later = time.time() + DAY
    os.utime(f, (later, later))
    g = graph.build(study, conn)
    assert {n["slug"]: n["studied"] for n in g["nodes"]}["graph-beta"] is False


def test_build_without_a_connection_still_reports_the_shape(study):
    g = graph.build(study, None)
    assert len(g["nodes"]) == 4 and len(g["edges"]) == 4
    assert all(n["studied"] is False and n["notes"] == 0 for n in g["nodes"])


def test_build_survives_a_db_without_the_study_tables(study, tmp_path):
    """`jobscout.graph check` may meet a DB older than these tables. Losing
    the studied rings is acceptable; refusing to draw the graph is not."""
    bare = sqlite3.connect(tmp_path / "bare.db")
    try:
        g = graph.build(study, bare)
    finally:
        bare.close()
    assert len(g["nodes"]) == 4
    assert all(n["studied"] is False and n["notes"] == 0 for n in g["nodes"])


def test_build_on_a_folder_with_no_topics(tmp_path):
    g = graph.build(tmp_path / "nothing-here", None)
    assert g == {"nodes": [], "edges": [], "warnings": []}


# ── split_frontmatter ───────────────────────────────────────────────

def test_split_frontmatter_reads_a_leading_block():
    meta, body = graph.split_frontmatter(
        "---\ntrack: dsa\nrelated: [a, b]\n---\n# Title\n\ntext\n")
    assert meta == {"track": "dsa", "related": ["a", "b"]}
    assert body == "# Title\n\ntext\n"


def test_split_frontmatter_handles_crlf():
    meta, body = graph.split_frontmatter("---\r\ntrack: dsa\r\n---\r\n# Title\r\n")
    assert meta == {"track": "dsa"} and body.startswith("# Title")


def test_split_frontmatter_without_a_block_returns_the_text_whole():
    text = "# Title\n\nno metadata here\n"
    assert graph.split_frontmatter(text) == ({}, text)


def test_split_frontmatter_ignores_a_block_that_is_not_at_the_top():
    text = "# Title\n\n---\ntrack: dsa\n---\n"
    assert graph.split_frontmatter(text) == ({}, text)


def test_split_frontmatter_survives_broken_yaml():
    """The routine writes these files unattended; a bad block must degrade to
    'no metadata', never take the reader page down."""
    text = "---\ntrack: [unclosed\ntags: 'oops\n---\n# Title\n"
    assert graph.split_frontmatter(text) == ({}, text)


def test_split_frontmatter_rejects_yaml_that_is_not_a_mapping():
    text = "---\n- one\n- two\n---\n# Title\n"
    assert graph.split_frontmatter(text) == ({}, text)


def test_split_frontmatter_treats_an_empty_block_as_no_metadata():
    text = "---\n---\n# Title\n"
    assert graph.split_frontmatter(text) == ({}, text)


def test_split_frontmatter_on_empty_input():
    assert graph.split_frontmatter("") == ({}, "")


# ── track_of / title_of ─────────────────────────────────────────────

@pytest.mark.parametrize("slug, track", [
    ("dsa-sliding-window", "dsa"),
    ("resume-auth-system", "resume"),
    ("system-design-rate-limiting", "system-design"),
    ("dotnet-async-concurrency", "backend"),
    ("sql-indexing", "backend"),
    ("api-design-contracts", "backend"),
    ("llm-evaluation", "llm-infra"),
    ("rag-retrieval", "llm-infra"),
    ("agentic-orchestration", "llm-infra"),
    ("something-else-entirely", "unknown"),
])
def test_track_of_falls_back_to_the_slug_prefix(slug, track):
    assert graph.track_of(slug, {}) == track
    assert graph.track_of(slug) == track


def test_track_of_prefers_the_frontmatter():
    assert graph.track_of("dsa-anything", {"track": " system-design "}) == "system-design"
    assert graph.track_of("dsa-anything", {"track": ""}) == "dsa"
    assert graph.track_of("dsa-anything", {"track": None}) == "dsa"


def test_title_of_takes_the_h1_then_the_slug():
    assert graph.title_of("# Real Title\n\nbody", "some-slug") == "Real Title"
    assert graph.title_of("no heading here", "some-slug") == "Some Slug"
    assert graph.title_of("## Concept\n\n# Later H1\n", "s") == "Later H1"


# ── neighbours ──────────────────────────────────────────────────────

def test_neighbors_unions_both_directions_sorted_by_title(study, conn):
    g = graph.build(study, conn)
    # alpha points at beta; beta and gamma point back at alpha
    assert [n["slug"] for n in graph.neighbors(g, "graph-alpha")] == \
        ["graph-beta", "graph-gamma"]
    assert [n["slug"] for n in graph.neighbors(g, "graph-beta")] == \
        ["graph-alpha", "graph-gamma"]
    assert [n["slug"] for n in graph.neighbors(g, "graph-gamma")] == \
        ["graph-alpha", "graph-beta"]
    assert graph.neighbors(g, "resume-graph-drill") == []
    assert graph.neighbors(g, "not-a-topic") == []


def test_neighbors_returns_whole_nodes(study, conn):
    g = graph.build(study, conn)
    first = graph.neighbors(g, "graph-alpha")[0]
    assert first["title"] == "Beta, the second fixture topic"
    assert first["track"] == "llm-infra"


# ── repair ──────────────────────────────────────────────────────────

def test_repair_prepends_a_block_and_leaves_the_mtime_alone(study):
    f = study / "topics" / "graph-gamma.md"
    before_mtime = f.stat().st_mtime
    before_text = f.read_text()

    assert graph.repair(study) == ["graph-gamma"]

    assert abs(f.stat().st_mtime - before_mtime) < 1, \
        "adding metadata is not deepening a topic: mtime must survive"
    meta, body = graph.split_frontmatter(f.read_text())
    assert meta["track"] == "unknown"        # graph-gamma matches no prefix
    assert meta["tags"] == [] and meta["related"] == []
    stamp = date.fromtimestamp(before_mtime).isoformat()
    assert str(meta["created"]) == stamp and str(meta["updated"]) == stamp
    assert body.strip() == before_text.strip()


def test_repair_leaves_files_that_already_have_a_block(study):
    f = study / "topics" / "graph-alpha.md"
    before = f.read_text()
    graph.repair(study)
    assert f.read_text() == before


def test_repair_is_idempotent_and_clears_the_warning(study, conn):
    graph.repair(study)
    assert graph.repair(study) == []
    g = graph.build(study, conn)
    assert not [w for w in g["warnings"] if "no frontmatter" in w]
    # and the body it wrapped still carries its inline edge
    assert ("graph-gamma", "graph-alpha") in edge_set(g, "link")


# ── A4: the real folder, reported and never asserted on ─────────────

def test_real_study_folder_report(capsys):
    """Runs only where the private study folder exists (never in CI). It
    prints the health warnings and asserts nothing about their content -
    the graph is a report here, not a gate."""
    sdir = settings.study_dir()
    if not (sdir / "topics").is_dir():
        pytest.skip("no study folder in this checkout")
    g = graph.build(sdir, None)
    with capsys.disabled():
        print(f"\nreal study folder: {len(g['nodes'])} topics, "
              f"{len(g['edges'])} edges, {len(g['warnings'])} warning(s)")
        for w in g["warnings"]:
            print(f"  {w}")
    assert isinstance(g["nodes"], list) and isinstance(g["edges"], list)
