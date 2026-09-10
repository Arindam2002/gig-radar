"""Version-scoped publishing to the archive site.

Everything here runs against the synthetic four-topic folder under
fixtures/study_publish, copied into tmp_path because the tests flag, unflag,
rewrite and delete. The fixture is: alpha and beta, two technical topics that
link each other and both get approved; delta, technical but never approved,
which is what alpha's second link degrades to; and a resume drill, approved
on purpose so the track exclusion has something to refuse.

Nothing in this file reads or copies the real study folder except the last
test, which only reports on it.
"""
import shutil
from pathlib import Path

import pytest
import yaml

from jobscout import db, publish, settings

FIXTURES = Path(__file__).parent / "fixtures" / "study_publish"


@pytest.fixture()
def study(tmp_path):
    dst = tmp_path / "study"
    shutil.copytree(FIXTURES, dst)
    return dst


@pytest.fixture()
def conn(tmp_path):
    c = db.init_db(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture()
def out(tmp_path):
    return tmp_path / "content" / "study"


def topic(study: Path, slug: str) -> Path:
    return study / "topics" / f"{slug}.md"


def approve(conn, study: Path, slug: str):
    """Flag a topic at the version currently on disk, the way the dashboard
    toggle does."""
    db.set_publish(conn, slug, publish.content_hash(topic(study, slug).read_text()))


def pages(out: Path) -> set:
    return {p.name for p in out.glob("*.md")}


# ── the db layer ────────────────────────────────────────────────────

def test_set_clear_and_read_the_publish_map(conn):
    assert db.publish_map(conn) == {}
    db.set_publish(conn, "alpha", "hash-one")
    db.set_publish(conn, "beta", "hash-two")
    got = db.publish_map(conn)
    assert set(got) == {"alpha", "beta"}
    assert got["alpha"]["content_hash"] == "hash-one"
    assert got["alpha"]["reviewed_at"]                    # stamped on approval
    db.set_publish(conn, "alpha", "hash-three")           # re-review, one row
    assert db.publish_map(conn)["alpha"]["content_hash"] == "hash-three"
    db.clear_publish(conn, "alpha")
    assert set(db.publish_map(conn)) == {"beta"}
    db.clear_publish(conn, "nobody")                      # a no-op, not an error


def test_publish_table_created_on_plain_connect(tmp_path):
    """The dashboard opens with connect(), not init_db(): a DB from before
    this table existed must still take a flag."""
    path = tmp_path / "old.db"
    c = db.connect(path)
    c.execute("CREATE TABLE study_progress (slug TEXT PRIMARY KEY, completed_at TEXT)")
    c.commit()
    assert db.publish_map(c) == {}
    db.set_publish(c, "x", "h")
    assert db.publish_map(c)["x"]["content_hash"] == "h"
    c.close()


def test_content_hash_is_the_whole_file(study):
    f = topic(study, "publish-alpha")
    before = publish.content_hash(f.read_text())
    assert before == publish.content_hash(f.read_text())          # stable
    f.write_text(f.read_text() + "\none more line\n")
    assert publish.content_hash(f.read_text()) != before
    # frontmatter counts too: a retagged topic is a different version
    f2 = topic(study, "publish-beta")
    text = f2.read_text()
    assert (publish.content_hash(text.replace("tags: [beta]", "tags: [b]"))
            != publish.content_hash(text))


# ── D1: the shape of an export ──────────────────────────────────────

@pytest.fixture()
def d1(study, conn, out):
    for slug in ("publish-alpha", "publish-beta", "resume-publish-drill"):
        approve(conn, study, slug)
    return publish.export(study, out, conn)


def test_d1_exports_only_the_approved_technical_topics(d1, out):
    assert d1.exported == ["publish-alpha", "publish-beta"]
    # the two pages, plus the generated concept sheet that always rides along
    assert pages(out) == {"publish-alpha.md", "publish-beta.md",
                          publish.CONCEPTS_PAGE}
    assert d1.ok and d1.refused == []
    reasons = dict(d1.skipped)
    assert "resume" in reasons["resume-publish-drill"]
    assert "publish-delta" not in reasons                 # never flagged at all


def test_d1_wikilinks_only_between_exported_topics(d1, out):
    alpha = (out / "publish-alpha.md").read_text()
    assert "[[publish-beta|beta]]" in alpha
    # delta is not in the export set: the link degrades to its own words
    assert "publish-delta" not in alpha
    assert "[delta]" not in alpha and "delta," in alpha
    assert "[[publish-alpha|alpha]]" in (out / "publish-beta.md").read_text()


def test_d1_related_section_is_filtered_the_same_way(d1, out):
    alpha = (out / "publish-alpha.md").read_text()
    assert "## Related" in alpha
    related = alpha.split("## Related", 1)[1]
    assert "[[publish-beta|Beta, the second publishable topic]]" in related
    assert "publish-delta" not in related


def test_d1_drops_the_routines_private_sections_and_the_h1(d1, out):
    alpha = (out / "publish-alpha.md").read_text()
    for gone in ("Deepen next time", "Deepened 2026-01-04",
                 "private working note", "Private revision log"):
        assert gone not in alpha
    assert "Answered follow-ups" not in (out / "publish-beta.md").read_text()
    assert "# Alpha, the first publishable topic" not in alpha.split("---")[2]
    assert "## Concept" in alpha                          # the article survives


def test_d1_frontmatter_is_quartz_shaped(d1, out):
    meta = yaml.safe_load((out / "publish-alpha.md").read_text().split("---")[1])
    assert meta["title"] == "Alpha, the first publishable topic"
    assert meta["tags"] == ["alpha", "fixture", "llm-infra"]   # tags + track
    assert str(meta["date"]) == "2026-01-05"                   # frontmatter updated


def test_d1_appends_the_deck_as_callouts_and_omits_it_when_there_is_none(d1, out):
    alpha = (out / "publish-alpha.md").read_text()
    assert "## Flashcards" in alpha
    assert "> [!question]- What does the alpha fixture deck prove?" in alpha
    assert "> That a deck renders as collapsible callouts on the site." in alpha
    assert alpha.count("[!question]-") == 2
    assert "Flashcards" not in (out / "publish-beta.md").read_text()


def test_d1_copies_and_embeds_the_diagram(d1, out):
    alpha = (out / "publish-alpha.md").read_text()
    assert "![Overview](publish-alpha.svg)" in alpha
    # right after the frontmatter, before any prose
    assert alpha.split("---\n")[2].lstrip().startswith("![Overview]")
    assert (out / "diagrams" / "publish-alpha.svg").is_file()
    assert not (out / "diagrams" / "publish-beta.svg").exists()


def test_d1_writes_the_manifest(d1, out):
    manifest = yaml.safe_load((out / publish.MANIFEST).read_text())
    assert set(manifest) == {"publish-alpha", "publish-beta",
                             publish.CONCEPTS_KEY}
    assert manifest["publish-alpha"]["content_hash"]
    assert manifest["publish-alpha"]["reviewed_at"]


# ── D2: the version rule ────────────────────────────────────────────

def test_d2_a_topic_changed_after_review_is_skipped_with_a_warning(
        study, conn, out):
    approve(conn, study, "publish-alpha")
    approve(conn, study, "publish-beta")
    f = topic(study, "publish-alpha")
    f.write_text(f.read_text() + "\nThe routine deepened this overnight.\n")

    result = publish.export(study, out, conn)
    assert result.exported == ["publish-beta"]
    assert "publish-alpha.md" not in pages(out)
    assert "changed since you reviewed it" in dict(result.skipped)["publish-alpha"]
    # and beta's wikilink to alpha degraded, because alpha is not on the site
    assert "[[publish-alpha" not in (out / "publish-beta.md").read_text()

    approve(conn, study, "publish-alpha")                 # re-review it
    assert "publish-alpha" in publish.export(study, out, conn).exported


# ── D3: the leak check ──────────────────────────────────────────────

def leaky(study: Path, slug: str, body: str):
    """A technical topic carrying something that must never be published."""
    topic(study, slug).write_text(
        "---\ntrack: backend\ntags: []\nrelated: []\n"
        "created: 2026-01-01\nupdated: 2026-01-08\n---\n"
        f"# {slug}\n\n## Concept\n\n{body}\n")


def test_d3_a_marker_refuses_the_file_and_the_run(study, conn, out):
    leaky(study, "publish-leaky", "## Interviewer probes\n\n- what baseline?")
    approve(conn, study, "publish-leaky")
    approve(conn, study, "publish-beta")

    result = publish.export(study, out, conn)
    assert not result.ok
    assert result.exported == ["publish-beta"]
    assert "publish-leaky.md" not in pages(out)
    assert "Interviewer probes" in dict(result.refused)["publish-leaky"][0]


def test_d3_a_link_to_a_resume_drill_refuses_the_file(study, conn, out):
    leaky(study, "publish-linky",
          "See [[resume-publish-drill|the drill]] for the numbers.")
    approve(conn, study, "publish-linky")
    approve(conn, study, "publish-beta")

    result = publish.export(study, out, conn)
    assert not result.ok
    assert "publish-linky.md" not in pages(out)
    assert "resume-publish-drill" in dict(result.refused)["publish-linky"][0]


def test_d3_the_fill_marker_refuses_too(study, conn, out):
    leaky(study, "publish-fill", "Baseline was [FILL: a number you can defend].")
    approve(conn, study, "publish-fill")
    result = publish.export(study, out, conn)
    assert not result.ok and result.exported == []


def test_d3_a_company_name_only_warns(study, conn, out):
    db.upsert_company(conn, "Zephyrite Analytics")
    approve(conn, study, "publish-alpha")
    approve(conn, study, "publish-beta")

    result = publish.export(study, out, conn)
    assert result.ok and result.refused == []
    assert "publish-alpha.md" in pages(out)               # written anyway
    assert dict(result.warned)["publish-alpha"] == ["Zephyrite Analytics"]
    assert "publish-beta" not in dict(result.warned)


def test_d3_short_names_and_the_stoplist_never_warn(study, conn, out):
    for name in ("Meta", "NVIDIA", "Develop", "Target"):
        db.upsert_company(conn, name)
    names = publish.company_names(conn)
    assert names == []
    # and a technical topic that names a vendor stays clean
    leaky(study, "publish-vendor", "We serve on NVIDIA GPUs with vLLM.")
    approve(conn, study, "publish-vendor")
    result = publish.export(study, out, conn)
    assert result.ok and result.warned == []


def test_d3_company_matching_is_word_bounded(study, conn, out):
    db.upsert_company(conn, "Robus")                      # sits inside "robust"
    leaky(study, "publish-robust", "The pipeline is robust and industrious.")
    approve(conn, study, "publish-robust")
    result = publish.export(study, out, conn)
    assert result.ok and result.warned == []


# ── D3b: the real folder, reported and never copied ─────────────────

def test_d3b_real_study_folder_audit(capsys):
    """Runs only where the private study folder exists (never in CI). It
    renders every technical topic as if it were approved and prints the
    company warnings; the refusal list is the assertion, because a refusal
    means a drill reached the door."""
    sdir = settings.study_dir()
    if not (sdir / "topics").is_dir():
        pytest.skip("no study folder in this checkout")
    conn = db.connect() if Path(db.DB_PATH).exists() else None
    try:
        result = publish.audit(sdir, conn)
    finally:
        if conn is not None:
            conn.close()
    with capsys.disabled():
        print(f"\nreal study folder: {len(result.exported)} technical topic(s) "
              f"render clean, {len(result.skipped)} drill(s) excluded, "
              f"{len(result.refused)} refusal(s)")
        for slug, names in result.warned:
            print(f"  ! {slug}: {', '.join(names)}")
        for slug, why in result.refused:
            print(f"  x {slug}: {'; '.join(why)}")
    assert result.refused == []


# ── D4: unpublishing is a deletion, and only ever the right one ─────

def test_d4_unflagging_deletes_the_page_and_its_diagram(study, conn, out):
    approve(conn, study, "publish-alpha")
    approve(conn, study, "publish-beta")
    publish.export(study, out, conn)
    assert (out / "publish-alpha.md").is_file()
    assert (out / "diagrams" / "publish-alpha.svg").is_file()

    # the two files the export must never touch: a hand-written post in the
    # sibling blog section, and an unrelated file inside the study section
    blog = out.parent / "blog"
    blog.mkdir(parents=True, exist_ok=True)
    (blog / "post.md").write_text("# A post I wrote myself\n")
    (out / "keep.md").write_text("not mine to delete\n")

    db.clear_publish(conn, "publish-alpha")
    result = publish.export(study, out, conn)

    assert result.exported == ["publish-beta"]
    assert not (out / "publish-alpha.md").exists()
    assert not (out / "diagrams" / "publish-alpha.svg").exists()
    assert any("publish-alpha.md" in p for p in result.deleted)
    assert (blog / "post.md").read_text() == "# A post I wrote myself\n"
    assert (out / "keep.md").read_text() == "not mine to delete\n"
    assert yaml.safe_load((out / publish.MANIFEST).read_text()).keys() == {
        "publish-beta", publish.CONCEPTS_KEY}


def test_d4_a_manifest_slug_that_is_a_path_is_ignored(study, conn, out):
    approve(conn, study, "publish-beta")
    publish.export(study, out, conn)
    (out.parent / "blog").mkdir(parents=True, exist_ok=True)
    (out.parent / "blog" / "post.md").write_text("mine\n")
    (out / publish.MANIFEST).write_text(yaml.safe_dump(
        {"../blog/post": {"reviewed_at": "x", "content_hash": "y"},
         "publish-beta": {"reviewed_at": "x", "content_hash": "y"}}))

    db.clear_publish(conn, "publish-beta")
    publish.export(study, out, conn)
    assert (out.parent / "blog" / "post.md").is_file()
    assert not (out / "publish-beta.md").exists()


def test_d4_a_topic_whose_file_vanished_is_skipped_not_crashed(
        study, conn, out):
    approve(conn, study, "publish-alpha")
    approve(conn, study, "publish-beta")
    publish.export(study, out, conn)
    topic(study, "publish-alpha").unlink()

    result = publish.export(study, out, conn)
    assert result.exported == ["publish-beta"]
    assert "no topic file" in dict(result.skipped)["publish-alpha"]
    assert not (out / "publish-alpha.md").exists()


# ── check mode writes nothing ───────────────────────────────────────

def test_check_reports_without_writing(study, conn, out):
    approve(conn, study, "publish-alpha")
    approve(conn, study, "publish-beta")
    result = publish.export(study, out, conn, dry_run=True)
    assert result.exported == ["publish-alpha", "publish-beta"]
    assert not out.exists()


# ── C2: the concept sheet goes out with the pages ───────────────────
#
# The publish fixture's topics carry no `concepts` key: it was written before
# the concept layer existed, and the D-tests pin its exact bytes. So these
# tests name concepts on the tmp_path COPY, right before approving it, which
# is also the honest order - a version is approved after it is written.

def name_concepts(study: Path, slug: str, *names):
    """Add a `concepts:` line to a topic's frontmatter in the copy."""
    f = topic(study, slug)
    head, rest = f.read_text().split("\n---\n", 1)
    f.write_text(f"{head}\nconcepts: [{', '.join(names)}]\n---\n{rest}")


@pytest.fixture()
def c2(study, conn, out):
    """Alpha and beta approved, each naming a concept of its own and one they
    share, so the sheet has both a per-topic list and a shared entry. Delta
    names a concept too and is never approved, which is what the "exported
    topics only" rule has to leave out."""
    name_concepts(study, "publish-alpha", "covering index", "shared concept")
    name_concepts(study, "publish-beta", "connection pool", "shared concept")
    name_concepts(study, "publish-delta", "delta only concept")
    for slug in ("publish-alpha", "publish-beta"):
        approve(conn, study, slug)
    return publish.export(study, out, conn)


def sheet_text(out: Path) -> str:
    return (out / publish.CONCEPTS_PAGE).read_text()


def test_c2_the_sheet_covers_the_exported_topics_and_nothing_else(c2, out):
    text = sheet_text(out)
    assert c2.ok and c2.refused == []
    assert "**covering index**" in text
    assert "**connection pool**" in text
    assert "delta only concept" not in text            # never approved
    assert "drill" not in text.lower()                 # never exported at all
    # every wikilink on the sheet resolves to a page this run actually wrote
    targets = {m.group(1) for m in publish._WIKILINK.finditer(text)}
    assert targets == {"publish-alpha", "publish-beta"}
    for slug in targets:
        assert (out / f"{slug}.md").is_file()


def test_c2_the_sheet_carries_none_of_the_local_extras(c2, out):
    """The archive copy is the vocabulary, not the reading history."""
    text = sheet_text(out)
    assert "Generated from the published topics." in text
    assert "Study order" not in text
    assert "studied" not in text                       # "not studied" too
    assert "question" not in text and "round" not in text
    assert "](topics/" not in text                     # wikilinks, not md links
    assert "<a id=" not in text                        # anchors are local-only
    assert "## Shared concepts" in text
    assert "**shared concept**" in text


def test_c2_the_sheet_is_in_the_manifest_under_the_reserved_key(c2, conn, out):
    manifest = yaml.safe_load((out / publish.MANIFEST).read_text())
    assert set(manifest) == {"publish-alpha", "publish-beta",
                             publish.CONCEPTS_KEY}
    entry = manifest[publish.CONCEPTS_KEY]
    assert entry["reviewed_at"]                        # stamped when written
    assert entry["content_hash"] == publish.content_hash(sheet_text(out))
    # and it is not, and never was, a row in study_publish
    assert publish.CONCEPTS_KEY not in db.publish_map(conn)


def test_c2_unflagging_the_last_topic_takes_the_sheet_down(study, conn, out):
    name_concepts(study, "publish-alpha", "covering index", "shared concept")
    approve(conn, study, "publish-alpha")
    publish.export(study, out, conn)
    assert (out / publish.CONCEPTS_PAGE).is_file()

    db.clear_publish(conn, "publish-alpha")
    result = publish.export(study, out, conn)
    assert result.exported == [] and result.sheet == ""
    assert not (out / publish.CONCEPTS_PAGE).exists()
    assert any(publish.CONCEPTS_PAGE in p for p in result.deleted)
    assert yaml.safe_load((out / publish.MANIFEST).read_text()) in ({}, None)


def test_c2_a_marker_in_a_concept_name_refuses_the_sheet_only(
        study, conn, out):
    """A drill marker can only reach the site through a concept name here, so
    that is where it is planted. The pages are innocent and still go out; the
    sheet does not, and the run ends not-ok."""
    name_concepts(study, "publish-alpha", "covering index", "The claim")
    name_concepts(study, "publish-beta", "connection pool", "shared concept")
    for slug in ("publish-alpha", "publish-beta"):
        approve(conn, study, slug)

    result = publish.export(study, out, conn)
    assert result.exported == ["publish-alpha", "publish-beta"]
    assert (out / "publish-alpha.md").is_file()
    assert not (out / publish.CONCEPTS_PAGE).exists()
    assert result.sheet == ""
    assert not result.ok                               # main() exits non-zero
    why = dict(result.refused)[publish.CONCEPTS_PAGE]
    assert any("The claim" in line for line in why)
    manifest = yaml.safe_load((out / publish.MANIFEST).read_text())
    assert publish.CONCEPTS_KEY not in manifest


def test_c2_a_topic_slug_called_concepts_is_refused(study, conn, out):
    """It would write over the generated sheet, so it never gets the chance."""
    src = topic(study, "publish-beta").read_text()
    (study / "topics" / "concepts.md").write_text(src)
    db.set_publish(conn, "concepts", publish.content_hash(src))
    approve(conn, study, "publish-alpha")

    result = publish.export(study, out, conn)
    assert "concepts" not in result.exported
    assert not result.ok
    assert "reserved" in " ".join(dict(result.refused)["concepts"])
    assert not publish._SAFE_SLUG.match("concepts")
    # the audit path refuses it too, with no flag set at all
    assert "concepts" in dict(publish.audit(study, conn).refused)


def test_c2_check_mentions_the_sheet_without_writing_it(
        study, conn, out, capsys):
    name_concepts(study, "publish-alpha", "covering index", "shared concept")
    approve(conn, study, "publish-alpha")
    result = publish.export(study, out, conn, dry_run=True)
    assert result.sheet                                # rendered, not written
    assert not out.exists()
    publish._report(result, True)
    assert f"+ {publish.CONCEPTS_PAGE}" in capsys.readouterr().out
