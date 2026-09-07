"""Flashcard decks: the sidecar parser and the deck health report.

Every fixture here is written in the test, synthetic and unrelated to the
private study folder. The last test looks at the real folder if it happens to
be there, prints what it finds, and asserts nothing about the content.
"""
from pathlib import Path

import pytest

from jobscout import cards, settings

GOOD = """\
# Alpha topic - cards

- Q: What does a token bucket bound?
  A: Burst (capacity b) and sustained rate (r/sec); both are the SLO knobs.

- Q: Why is a queue with no deadline a liability?
  A: Requests wait past the point the caller cares, so the work is wasted.
"""


def deck(*pairs) -> str:
    return "\n".join(f"- Q: {q}\n  A: {a}\n" for q, a in pairs)


def write_topic(study: Path, slug: str, track: str):
    (study / "topics").mkdir(parents=True, exist_ok=True)
    (study / "topics" / f"{slug}.md").write_text(
        f"---\ntrack: {track}\ntags: []\nrelated: []\n"
        f"created: 2026-01-01\nupdated: 2026-01-01\n---\n"
        f"# {slug}\n\n## Concept\nSynthetic fixture body.\n")


def write_deck(study: Path, slug: str, n: int):
    (study / "cards").mkdir(parents=True, exist_ok=True)
    (study / "cards" / f"{slug}.md").write_text(
        deck(*[(f"Question {i} for {slug}?", f"Answer {i}.") for i in range(n)]))


# ── C1: the parser ──────────────────────────────────────────────────

def test_parse_reads_the_documented_shape():
    got = cards.parse(GOOD)
    assert [c["q"] for c in got] == [
        "What does a token bucket bound?",
        "Why is a queue with no deadline a liability?"]
    assert got[0]["a"].startswith("Burst (capacity b)")
    assert got[1]["a"].endswith("the work is wasted.")


def test_parse_ignores_a_leading_title_and_comments():
    text = ("# Deck for the alpha topic\n"
            "<!-- written by the routine, do not edit by hand -->\n"
            "\n" + deck(("Only question?", "Only answer.")))
    assert cards.parse(text) == [{"q": "Only question?", "a": "Only answer."}]


def test_parse_joins_a_multi_line_answer_with_spaces():
    text = ("- Q: What breaks first under backpressure?\n"
            "  A: The unbounded queue: it absorbs load until memory runs out,\n"
            "     then everything fails at once instead of the slowest 1%.\n")
    got = cards.parse(text)
    assert len(got) == 1
    assert got[0]["a"] == ("The unbounded queue: it absorbs load until memory "
                           "runs out, then everything fails at once instead "
                           "of the slowest 1%.")
    assert "\n" not in got[0]["a"]


def test_parse_skips_malformed_bullets():
    """Half a card teaches nothing: an answer with no question before it, a
    question nothing answers, and an empty question are all dropped, and the
    one well-formed card between them survives."""
    text = ("  A: An answer nobody asked for.\n"
            "\n"
            "- Q: A question with no answer at all?\n"
            "\n"
            "- Q: The one good card?\n"
            "  A: Yes, this one.\n"
            "\n"
            "- Q:\n"
            "  A: An answer to an empty question.\n")
    assert cards.parse(text) == [{"q": "The one good card?", "a": "Yes, this one."}]


def test_parse_of_nothing_is_an_empty_deck():
    assert cards.parse("") == []
    assert cards.parse("# Just a title\n\nsome prose, no cards\n") == []


# ── load / deck_path ────────────────────────────────────────────────

def test_deck_path_is_the_sidecar_next_to_topics(tmp_path):
    assert cards.deck_path(tmp_path, "alpha") == tmp_path / "cards" / "alpha.md"


def test_load_reads_the_sidecar(tmp_path):
    (tmp_path / "cards").mkdir()
    (tmp_path / "cards" / "alpha.md").write_text(GOOD)
    assert len(cards.load(tmp_path, "alpha")) == 2


def test_load_of_a_missing_deck_is_an_empty_list(tmp_path):
    assert cards.load(tmp_path, "no-such-topic") == []


# ── C1: the health report ───────────────────────────────────────────

@pytest.fixture()
def study(tmp_path):
    """Four topics: one healthy deck, one too short, one missing, and a
    resume drill that is meant to have no deck at all."""
    s = tmp_path / "study"
    write_topic(s, "cards-good", "backend")
    write_topic(s, "cards-thin", "llm-infra")
    write_topic(s, "cards-missing", "system-design")
    write_topic(s, "resume-cards-drill", "resume")
    write_deck(s, "cards-good", 7)
    write_deck(s, "cards-thin", 3)
    return s


def test_report_flags_missing_and_undersized_decks(study):
    lines = cards.report(study)
    joined = "\n".join(lines)
    assert len(lines) == 2, joined
    assert any(l.startswith("cards-missing:") and "no deck" in l for l in lines)
    assert any(l.startswith("cards-thin:") and "3 card(s)" in l for l in lines)


def test_report_never_asks_a_resume_drill_for_a_deck(study):
    assert not any("resume-cards-drill" in line for line in cards.report(study))


def test_report_is_quiet_when_every_deck_is_healthy(study):
    write_deck(study, "cards-thin", 6)           # the floor
    write_deck(study, "cards-missing", 10)       # the ceiling
    assert cards.report(study) == []


def test_report_flags_an_oversized_deck(study):
    write_deck(study, "cards-good", 11)
    assert any(l.startswith("cards-good:") and "11 card(s)" in l
               for l in cards.report(study))


# ── C4: the real folder, reported and never asserted on ─────────────

def test_real_study_folder_deck_report(capsys):
    """Runs only where the private study folder exists (never in CI). It
    prints the decks that are missing or the wrong size and asserts nothing
    about them - the routine fills the gaps over the following days."""
    sdir = settings.study_dir()
    if not (sdir / "topics").is_dir():
        pytest.skip("no study folder in this checkout")
    problems = cards.report(sdir)
    with capsys.disabled():
        print(f"\nreal study folder: {len(problems)} deck warning(s)")
        for line in problems:
            print(f"  {line}")
    assert isinstance(problems, list)
