"""The concept layer, against a synthetic eleven-topic study folder.

The fixture folder under fixtures/study_concepts is copied into tmp_path for
every test and every file is given an explicit mtime, because mtime is the
worksheet's last tie-breaker and a checkout's mtimes are whatever git felt
like. Nothing here touches the real study folder.

The fixture is built so each warning class has exactly one home:

    alpha     five concepts, no prerequisites, the root of the chain
    beta      needs alpha; spells alpha's "change tracking" as
              "change tracker" and shares "shared concept" with it
    gamma     needs beta, so alpha -> beta -> gamma is the chain case
    bare      no `concepts` key at all
    thin      two concepts (under the floor)
    many      eleven concepts (over the ceiling)
    ghost     a prerequisite with no file
    drilldep  a prerequisite that is a resume drill
    loop-one  needs loop-two ─┐ a cycle; loop-two was updated later, so
    loop-two  needs loop-one ─┘ loop-two is the file that loses its edge
    resume-concepts-drill     carries neither, and is never ordered
"""
import os
import shutil
from datetime import date, datetime
from pathlib import Path

import pytest

from jobscout import concepts, db, graph

FIXTURES = Path(__file__).parent / "fixtures" / "study_concepts"

# Oldest debt first. The worksheet breaks a tie on mtime, so the fixture
# pins one: alphabetical would make the assertion a coincidence.
MTIME_ORDER = [
    "concepts-alpha", "concepts-beta", "concepts-gamma", "concepts-bare",
    "concepts-thin", "concepts-many", "concepts-ghost", "concepts-drilldep",
    "concepts-loop-one", "concepts-loop-two", "resume-concepts-drill",
]

# Every fixture topic says `created: 2026-01-02`, so "now" in these tests is
# a date relative to that: three days on is business as usual, fifteen days
# on is the starvation escape.
SOON = date(2026, 1, 5)
LATER = date(2026, 1, 17)


@pytest.fixture()
def study(tmp_path):
    dst = tmp_path / "study"
    shutil.copytree(FIXTURES, dst)
    base = datetime(2026, 1, 2).timestamp()
    for i, slug in enumerate(MTIME_ORDER):
        f = dst / "topics" / f"{slug}.md"
        os.utime(f, (base + i, base + i))
    return dst


@pytest.fixture()
def conn(tmp_path):
    c = db.init_db(tmp_path / "t.db")
    yield c
    c.close()


def order_of(result, conn=None, now=SOON):
    return [r["slug"] for r in concepts.study_order(result, conn, now=now)["order"]]


def row_for(result, slug, conn=None, now=SOON):
    rows = concepts.study_order(result, conn, now=now)["order"]
    return next((r for r in rows if r["slug"] == slug), None)


# ── A1: what build returns ──────────────────────────────────────────

def test_one_entry_per_canonical_concept(study):
    """"change tracking" and "change tracker" are one idea. The entry keeps
    the first spelling in slug order as canonical and remembers both."""
    r = concepts.build(study, None)
    key = concepts.normalise("change tracking")
    assert key == concepts.normalise("change tracker")
    entry = r["concepts"][key]
    assert entry["canonical"] == "change tracking"       # alpha sorts first
    assert entry["names"] == ["change tracking", "change tracker"]
    assert entry["topics"] == ["concepts-alpha", "concepts-beta"]
    assert entry["id"] == concepts.concept_id(key) == "c:chang track"


def test_every_entry_is_keyed_by_its_own_normalised_name(study):
    r = concepts.build(study, None)
    for key, entry in r["concepts"].items():
        assert concepts.normalise(entry["canonical"]) == key
        assert entry["id"] == concepts.concept_id(key)
        assert entry["canonical"] == entry["names"][0]
        assert entry["topics"] == sorted(entry["topics"])


def test_a_shared_concept_is_one_node_naming_both_topics(study):
    r = concepts.build(study, None)
    shared = r["concepts"][concepts.normalise("shared concept")]
    assert shared["topics"] == ["concepts-alpha", "concepts-beta"]
    assert [c["canonical"] for c in r["concepts"].values()
            if len(c["topics"]) > 1] == ["change tracking", "shared concept"]


def test_topic_to_concept_edges(study):
    r = concepts.build(study, None)
    by_topic = {}
    for e in r["edges"]:
        assert e["kind"] == "concept" and e["target"].startswith("c:")
        by_topic.setdefault(e["source"], []).append(e["target"])
    assert by_topic["concepts-alpha"] == [
        concepts.concept_id(concepts.normalise(n)) for n in
        ("covering index", "execution plan", "change tracking",
         "write amplification", "shared concept")]
    # the shared concept is one target reached from two topics
    shared = concepts.concept_id(concepts.normalise("shared concept"))
    assert {e["source"] for e in r["edges"] if e["target"] == shared} == \
        {"concepts-alpha", "concepts-beta"}
    assert "concepts-bare" not in by_topic
    assert "resume-concepts-drill" not in by_topic
    assert len(r["edges"]) == len({(e["source"], e["target"]) for e in r["edges"]})


def test_topics_carry_the_graph_node_plus_the_frontmatter_lists(study):
    r = concepts.build(study, None)
    alpha = r["topics"]["concepts-alpha"]
    assert alpha["title"] == "Alpha, a synthetic backend topic"
    assert alpha["track"] == "backend" and alpha["words"] > 10
    assert (alpha["questions"], alpha["rounds"]) == (2, 1)     # from graph.depth_of
    assert alpha["prereqs"] == [] and len(alpha["concepts"]) == 5
    assert alpha["created"] == date(2026, 1, 2) and alpha["updated"] == "2026-01-03"
    assert r["topics"]["concepts-beta"]["prereqs"] == ["concepts-alpha"]
    assert r["topics"]["resume-concepts-drill"]["concepts"] == []


def test_effective_prereqs_drop_a_target_with_no_file(study):
    r = concepts.build(study, None)
    assert r["prereqs"]["concepts-ghost"] == []
    assert r["unresolved"] == {"concepts-ghost": ["concepts-nowhere"]}


def test_a_cycle_is_broken_deterministically(study):
    """Loop two was updated last, so loop two is the file that gives way."""
    r = concepts.build(study, None)
    assert r["cycles"] == [{"cycle": ["concepts-loop-one", "concepts-loop-two"],
                            "source": "concepts-loop-two",
                            "target": "concepts-loop-one"}]
    assert r["prereqs"]["concepts-loop-two"] == []
    assert r["prereqs"]["concepts-loop-one"] == ["concepts-loop-two"]
    again = concepts.build(study, None)
    assert again["cycles"] == r["cycles"] and again["prereqs"] == r["prereqs"]


def test_build_never_lints(study):
    """The dashboard builds this on every render; the report is the CLI's
    job. The result carries no warning list of its own."""
    r = concepts.build(study, None)
    assert "warnings" not in r
    assert concepts.lint(r), "the fixture has plenty to warn about"


def test_build_survives_a_folder_with_no_topics(tmp_path):
    r = concepts.build(tmp_path / "nothing-here", None)
    assert r["topics"] == {} and r["concepts"] == {} and r["edges"] == []
    assert r["prereqs"] == {} and r["cycles"] == []
    assert concepts.lint(r) == []


def test_build_carries_the_link_graph_it_was_built_on(study):
    r = concepts.build(study, None)
    assert {n["slug"] for n in r["graph"]["nodes"]} == set(r["topics"])


# ── A2: lint, and the pinned near-duplicate table ───────────────────

@pytest.mark.parametrize("first, second, flagged", [
    ("KV cache", "KV-cache paging", True),
    ("KV-cache paging", "paged KV cache", True),
    ("change tracking", "change tracker", True),
    ("covering index", "index", False),
    ("continuous batching", "batching", False),
    ("execution plan", "query plan", False),
])
def test_the_pinned_near_duplicate_table(first, second, flagged):
    """The table the rule was designed against. A string-similarity check
    fails the first row (0.55) and a plain token check fails the last three;
    token sets over stems is what gets all six right."""
    assert concepts.near_duplicate(first, second) is flagged
    assert concepts.near_duplicate(second, first) is flagged


def test_near_duplicate_ignores_names_with_nothing_in_them():
    assert concepts.near_duplicate("the", "of the") is False
    assert concepts.near_duplicate("", "index") is False


def test_normalise_reduces_spelling_to_one_key():
    assert concepts.normalise("KV-cache paging") == ("cach", "kv", "pag")
    assert concepts.normalise("Paging the KV Caches") == ("cach", "kv", "pag")
    assert concepts.normalise("batching vs streaming") == \
        concepts.normalise("streaming versus batching")
    assert concepts.normalise("  ") == ()


def test_lint_flags_a_topic_with_no_concepts(study):
    lines = concepts.lint(concepts.build(study, None))
    assert [ln for ln in lines if "no concepts" in ln] == \
        ["concepts-bare: no concepts in frontmatter"]


def test_lint_flags_too_few_and_too_many(study):
    lines = concepts.lint(concepts.build(study, None))
    counted = [ln for ln in lines if "a topic wants" in ln]
    assert counted == ["concepts-many: 11 concept(s); a topic wants 5 to 10",
                       "concepts-thin: 2 concept(s); a topic wants 5 to 10"]


def test_lint_flags_an_unresolvable_prereq_and_a_drill_prereq(study):
    lines = concepts.lint(concepts.build(study, None))
    assert [ln for ln in lines if "has no file" in ln] == \
        ["concepts-ghost: prereq 'concepts-nowhere' has no file"]
    drill = [ln for ln in lines if "resume drill" in ln]
    assert len(drill) == 1 and drill[0].startswith("concepts-drilldep:")


def test_lint_names_the_edge_it_dropped_from_the_cycle(study):
    lines = concepts.lint(concepts.build(study, None))
    assert [ln for ln in lines if ln.startswith("cycle ")] == [
        "cycle concepts-loop-one -> concepts-loop-two -> concepts-loop-one: "
        "dropped 'concepts-loop-one' from concepts-loop-two's prereqs"]


def test_lint_flags_the_fixtures_one_near_duplicate_pair_and_no_other(study):
    lines = concepts.lint(concepts.build(study, None))
    assert [ln for ln in lines if ln.startswith("near-duplicate")] == [
        "near-duplicate concepts: 'change tracking' ~ 'change tracker' "
        "(in concepts-alpha, concepts-beta)"]


def test_lint_asks_nothing_of_a_resume_drill(study):
    lines = concepts.lint(concepts.build(study, None))
    assert not [ln for ln in lines if ln.startswith("resume-concepts-drill:")]


def test_lint_reads_the_same_twice_running(study):
    r = concepts.build(study, None)
    assert concepts.lint(r) == concepts.lint(concepts.build(study, None))


# ── A3: overrides ───────────────────────────────────────────────────

def test_a_remove_drops_a_frontmatter_prereq(study, conn):
    db.add_override(conn, "concepts-beta", "concepts-alpha", "remove")
    r = concepts.build(study, conn)
    assert r["prereqs"]["concepts-beta"] == []
    assert r["overrides"] == {"concepts-beta": {"concepts-alpha": "remove"}}
    assert row_for(r, "concepts-beta", conn)["ready"] is True
    # and gamma no longer owes alpha through beta
    assert row_for(r, "concepts-gamma", conn)["after"] == ["concepts-beta"]


def test_an_add_introduces_one(study, conn):
    db.add_override(conn, "concepts-thin", "concepts-alpha", "add")
    r = concepts.build(study, conn)
    assert r["prereqs"]["concepts-thin"] == ["concepts-alpha"]
    row = row_for(r, "concepts-thin", conn)
    assert row["ready"] is False and row["after"] == ["concepts-alpha"]
    assert order_of(r, conn).index("concepts-alpha") < \
        order_of(r, conn).index("concepts-thin")


def test_an_add_that_would_close_a_loop_is_reported_by_lint(study, conn):
    """Nothing refuses the write - the picker on the topic page is the guard.
    What lands here is a cycle, and a cycle is cut and named."""
    db.add_override(conn, "concepts-alpha", "concepts-gamma", "add")
    r = concepts.build(study, conn)
    cut = [c for c in r["cycles"]
           if set(c["cycle"]) == {"concepts-alpha", "concepts-beta", "concepts-gamma"}]
    assert len(cut) == 1
    assert any(ln.startswith("cycle ") and "concepts-gamma" in ln
               for ln in concepts.lint(r))
    assert concepts.would_cycle(concepts.build(study, None),
                                "concepts-alpha", "concepts-gamma") is True


def test_would_cycle_guards_the_add_picker(study):
    r = concepts.build(study, None)
    assert concepts.would_cycle(r, "concepts-alpha", "concepts-alpha") is True
    assert concepts.would_cycle(r, "concepts-alpha", "concepts-beta") is True
    assert concepts.would_cycle(r, "concepts-alpha", "concepts-gamma") is True
    assert concepts.would_cycle(r, "concepts-gamma", "concepts-alpha") is False
    assert concepts.would_cycle(r, "concepts-thin", "concepts-alpha") is False


def test_a_pair_says_one_thing_at_a_time(study, conn):
    db.add_override(conn, "concepts-beta", "concepts-alpha", "remove")
    db.add_override(conn, "concepts-beta", "concepts-alpha", "add")
    assert db.overrides_map(conn) == \
        {"concepts-beta": {"concepts-alpha": "add"}}
    assert concepts.build(study, conn)["prereqs"]["concepts-beta"] == \
        ["concepts-alpha"]


def test_clearing_an_override_gives_the_frontmatter_the_last_word(study, conn):
    db.add_override(conn, "concepts-beta", "concepts-alpha", "remove")
    db.clear_override(conn, "concepts-beta", "concepts-alpha")
    assert db.overrides_map(conn) == {}
    assert concepts.build(study, conn)["prereqs"]["concepts-beta"] == \
        ["concepts-alpha"]


def test_an_override_refuses_to_be_nonsense(conn):
    with pytest.raises(ValueError):
        db.add_override(conn, "a", "b", "maybe")
    with pytest.raises(ValueError):
        db.add_override(conn, "a", "a", "add")
    with pytest.raises(ValueError):
        db.add_override(conn, "", "b", "add")


def test_an_add_pointing_nowhere_is_dropped_and_reported(study, conn):
    db.add_override(conn, "concepts-thin", "concepts-nowhere", "add")
    r = concepts.build(study, conn)
    assert r["prereqs"]["concepts-thin"] == []
    assert "concepts-thin: prereq 'concepts-nowhere' has no file" in concepts.lint(r)


def test_build_survives_a_db_without_the_overrides_table(study, tmp_path):
    import sqlite3
    bare = sqlite3.connect(tmp_path / "bare.db")
    try:
        r = concepts.build(study, bare)
    finally:
        bare.close()
    assert r["prereqs"]["concepts-beta"] == ["concepts-alpha"]


# ── A5: study order ─────────────────────────────────────────────────

def test_all_unstudied_order_matches_the_stated_keys(study, conn):
    """Ready first, then fewest topics owed behind you, then oldest debt.
    Nothing is studied, so the ready set is exactly the roots of the DAG."""
    r = concepts.build(study, conn)
    assert order_of(r, conn) == [
        # ready, pending 0, in mtime order
        "concepts-alpha", "concepts-bare", "concepts-thin", "concepts-many",
        "concepts-ghost", "concepts-loop-two",
        # not ready, one topic owed, in mtime order
        "concepts-beta", "concepts-drilldep", "concepts-loop-one",
        # not ready, two owed
        "concepts-gamma",
    ]
    assert row_for(r, "concepts-gamma", conn)["after"] == \
        ["concepts-alpha", "concepts-beta"]
    assert row_for(r, "concepts-alpha", conn)["reason"] == "ready"
    assert row_for(r, "concepts-beta", conn)["reason"] == "after concepts-alpha"


def test_the_chain_with_the_middle_studied(study, conn):
    """a -> b -> c with b studied: the order is [a, c], and c owes a only."""
    db.set_study_done(conn, "concepts-beta", True)
    r = concepts.build(study, conn)
    chain = [s for s in order_of(r, conn)
             if s in {"concepts-alpha", "concepts-beta", "concepts-gamma"}]
    assert chain == ["concepts-alpha", "concepts-gamma"]
    gamma = row_for(r, "concepts-gamma", conn)
    assert gamma["ready"] is True                 # its own prerequisite is done
    assert gamma["after"] == ["concepts-alpha"]   # and it still owes alpha
    assert gamma["blocked_by"] == []
    assert concepts.study_order(r, conn, now=SOON)["studied"] == ["concepts-beta"]


def test_a_fifteen_day_old_leaf_is_promoted(study, conn):
    r = concepts.build(study, conn)
    at_fourteen = row_for(r, "concepts-gamma", conn, now=date(2026, 1, 16))
    assert at_fourteen["ready"] is False and at_fourteen["promoted"] is False
    promoted = row_for(r, "concepts-gamma", conn, now=LATER)
    assert promoted["ready"] is True and promoted["promoted"] is True
    assert "unstudied for over 14 days" in promoted["reason"]
    assert promoted["after"] == ["concepts-alpha", "concepts-beta"]
    # promotion changes readiness, not how much you owe: the order still
    # puts the topic owing nothing first
    assert order_of(r, conn, now=LATER)[0] == "concepts-alpha"
    assert order_of(r, conn, now=LATER)[-1] == "concepts-gamma"


def test_studied_topics_leave_the_order_and_are_listed(study, conn):
    db.set_study_done(conn, "concepts-alpha", True)
    r = concepts.build(study, conn)
    out = concepts.study_order(r, conn, now=SOON)
    assert "concepts-alpha" not in [row["slug"] for row in out["order"]]
    assert out["studied"] == ["concepts-alpha"]
    beta = row_for(r, "concepts-beta", conn)
    assert beta["ready"] is True and beta["after"] == []


def test_resume_drills_are_never_ordered(study, conn):
    r = concepts.build(study, conn)
    out = concepts.study_order(r, conn, now=SOON)
    assert "resume-concepts-drill" not in [row["slug"] for row in out["order"]]
    assert "resume-concepts-drill" not in out["studied"]


def test_the_order_re_reads_your_ticks_from_the_connection(study, conn):
    """The dashboard caches the build against the topic folder, and ticking a
    checkbox does not touch the folder. The tick still has to count."""
    r = concepts.build(study, conn)
    assert row_for(r, "concepts-beta", conn)["ready"] is False
    db.set_study_done(conn, "concepts-alpha", True)
    assert row_for(r, "concepts-beta", conn)["ready"] is True     # same result dict
    assert row_for(r, "concepts-beta", None)["ready"] is False    # no db, no news


def test_a_topic_rewritten_after_you_ticked_it_is_unstudied_again(study, conn):
    db.set_study_done(conn, "concepts-alpha", True)
    f = study / "topics" / "concepts-alpha.md"
    later = datetime.now().timestamp() + 86400
    os.utime(f, (later, later))
    r = concepts.build(study, conn)
    assert "concepts-alpha" in [row["slug"]
                                for row in concepts.study_order(r, conn)["order"]]


# ── new names for the daily brief ───────────────────────────────────

def test_every_name_is_new_when_there_is_no_sheet(study, tmp_path):
    r = concepts.build(study, None)
    fresh = concepts.new_names(r, tmp_path / "nothing" / "CONCEPTS.md")
    assert len(fresh) == len(r["concepts"])
    assert fresh == sorted(fresh, key=str.lower)


def test_only_the_names_the_sheet_has_never_heard_of(study, tmp_path):
    sheet = tmp_path / "CONCEPTS.md"
    sheet.write_text(
        "# Concepts\n\n## backend\n"
        "### [Alpha](topics/concepts-alpha.md)\n"
        "- **covering index** — one where every column is in the index.\n"
        "- **execution plan** — (no card yet)\n"
        "- **Change Trackers** — a respelling of a name already on the sheet.\n")
    fresh = concepts.new_names(concepts.build(study, None), sheet)
    assert "covering index" not in fresh
    assert "execution plan" not in fresh
    assert "change tracking" not in fresh          # a respelling is not a new name
    assert "shared concept" in fresh


def test_the_sheet_path_is_next_to_the_topics(tmp_path):
    assert concepts.sheet_path(tmp_path) == tmp_path / "CONCEPTS.md"


# ── the CLI ─────────────────────────────────────────────────────────

@pytest.fixture()
def cli(study, tmp_path, monkeypatch):
    monkeypatch.setenv("JOBSCOUT_STUDY", str(study))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "no-such.db")
    return None


def test_cli_check_reports_and_never_gates(cli, capsys):
    assert concepts._cli(["concepts", "check"]) == 0
    out = capsys.readouterr().out
    assert "11 topics" in out and "warning(s)" in out
    assert "concepts-bare: no concepts in frontmatter" in out
    assert "near-duplicate concepts:" in out
    assert "concept name(s) not yet on the sheet" in out


def test_cli_order_prints_a_reason_per_row(cli, capsys):
    """The CLI reads the clock, and the fixture was created in January 2026,
    so every blocked topic here is past the starvation escape. That is the
    point of the escape: nothing in a real base waits forever either."""
    assert concepts._cli(["concepts", "order"]) == 0
    out = capsys.readouterr().out
    assert "10 topic(s) to study, 0 studied" in out
    assert "1. concepts-alpha (ready)" in out
    assert "10. concepts-gamma (promoted; unstudied for over 14 days)" in out


def test_cli_says_so_when_it_does_not_know_the_command(cli, capsys):
    assert concepts._cli(["concepts", "sheet"]) == 0
    assert "Use: check | order" in capsys.readouterr().out


def test_the_module_reads_the_folder_it_is_pointed_at(cli, study):
    from jobscout import settings
    assert settings.study_dir() == study
    assert graph.topic_files(study)
