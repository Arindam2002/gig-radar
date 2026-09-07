"""Flashcard decks: one sidecar file per topic, parsed into Q/A pairs.

A deck lives at `study/cards/<slug>.md`, next to the topic rather than inside
it. The daily routine writes it; the article renderer never shows it; the
tutor reads it and never edits it. That separation is the whole point - the
routine rewrites topic files daily, so a `## Flashcards` section in the body
would be rewritten, buried under appended "Deepened" sections, and rendered
with its answers in plain sight.

The shape is one bullet per card, question first, answer indented under it:

    - Q: What does a token bucket bound?
      A: Burst (capacity b) and sustained rate (r/sec); both are SLO knobs.

An answer may run over further indented lines, which are joined with spaces.
Blank lines, a leading `# ` title and comment lines are ignored. A `Q:` with
no `A:`, or an `A:` with no `Q:` before it, is malformed and dropped: half a
card teaches nothing, and the parser must never raise on a file the routine
wrote on a bad day.

Resume drills (`resume-*`, `track: resume`) carry no deck, which is why
`report` skips them instead of listing them as missing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from jobscout import graph, settings

# "- Q: …" opens a card. The bullet marker may be "-" or "*" and may be
# indented; what matters is the "Q:" label.
_Q_LINE = re.compile(r"^[ \t]*[-*][ \t]+Q:[ \t]*(.*)$")
# "  A: …" answers the card above it. Written with two spaces; any indent is
# accepted, and an unindented one is caught here too so the state machine can
# decide whether it has a question to attach it to.
_A_LINE = re.compile(r"^[ \t]*A:[ \t]*(.*)$")

# A deck small enough to run through in a break, big enough to cover a topic.
MIN_CARDS = 6
MAX_CARDS = 10


def parse(text: str) -> list[dict]:
    """The cards in a deck file, in file order, as [{"q": …, "a": …}, …].

    Never raises: anything that is not a well-formed pair is skipped.
    """
    cards: list[dict] = []
    question: str | None = None
    answer: list[str] | None = None

    def flush():
        nonlocal question, answer
        if question and answer:
            joined = " ".join(part for part in answer if part).strip()
            if joined:
                cards.append({"q": question, "a": joined})
        question, answer = None, None

    for raw in (text or "").splitlines():
        line = raw.rstrip()

        m = _Q_LINE.match(line)
        if m:
            flush()                              # the previous card ends here
            question = m.group(1).strip() or None
            continue

        m = _A_LINE.match(line)
        if m:
            if question is None:                 # an answer to nothing
                continue
            if answer is None:
                answer = []
            answer.append(m.group(1).strip())
            continue

        if not line.strip():                     # blank lines are separators
            continue

        if answer is not None and line[:1] in (" ", "\t"):
            answer.append(line.strip())          # the answer runs on
            continue

        if question is not None and line[:1] in (" ", "\t"):
            continue                             # stray indent before the A:

        flush()                                  # a title, a comment, prose

    flush()
    return cards


def deck_path(study_dir: Path, slug: str) -> Path:
    return Path(study_dir) / "cards" / f"{slug}.md"


def load(study_dir: Path, slug: str) -> list[dict]:
    """The topic's deck, or an empty list when there is no deck file. A
    missing or unreadable deck is a topic without cards, not an error."""
    p = deck_path(study_dir, slug)
    try:
        return parse(p.read_text())
    except OSError:
        return []


def report(study_dir: Path) -> list[str]:
    """One line per technical topic whose deck is missing or the wrong size.

    A report, never a gate: the routine fills the gaps over the following
    days and nothing downstream needs a deck to work.
    """
    out: list[str] = []
    for f in graph.topic_files(study_dir):
        slug = f.stem
        try:
            meta, _ = graph.split_frontmatter(f.read_text())
        except OSError:
            continue
        if graph.track_of(slug, meta) == "resume":
            continue                             # drills are drilled, not carded
        p = deck_path(study_dir, slug)
        if not p.exists():
            out.append(f"{slug}: no deck at cards/{slug}.md")
            continue
        n = len(load(study_dir, slug))
        if n < MIN_CARDS or n > MAX_CARDS:
            out.append(f"{slug}: {n} card(s); a deck wants "
                       f"{MIN_CARDS} to {MAX_CARDS}")
    return out


def _cli(argv: list[str]) -> int:
    study = settings.study_dir()
    problems = report(study)
    if not problems:
        print("Every technical topic has a deck of "
              f"{MIN_CARDS} to {MAX_CARDS} cards.")
        return 0
    print(f"{len(problems)} deck warning(s):")
    for line in problems:
        print(f"  {line}")
    return 0                                     # a report, not a gate


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
