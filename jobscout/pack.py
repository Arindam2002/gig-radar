"""Writing pack: everything you have on one topic, in one markdown file.

`python -m jobscout.pack <slug>` collects the topic body, its flashcard deck,
its diagram, your highlights and notes from the DB, and the session-note
sections that mention the topic, into `study/packs/<slug>.md`.

The pack is raw material, not a draft: it is private (it lives under the
gitignored study folder) and the post that comes out of it is yours to write.

Highlights are stored as quoted text plus context (see db.study_notes), not
offsets, so "article order" here means the position of the quote inside the
topic body, matched on whitespace-normalised text. Quotes that no longer
appear in the body (the routine rewrote that paragraph) sort last, in the
order they were created.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.S)
_H1 = re.compile(r"^#\s+(.+?)\s*$", re.M)
_WS = re.compile(r"\s+")

# slug prefix -> track, longest prefix wins (see plan WS-E; frontmatter beats
# this the moment WS-A lands and the routine writes a `track:` key)
TRACK_PREFIXES = {
    "dsa-": "dsa",
    "resume-": "resume",
    "system-design-": "system-design",
    "dotnet-": "backend",
    "sql-": "backend",
    "api-": "backend",
    "llm-": "llm-infra",
    "rag-": "llm-infra",
    "agentic-": "llm-infra",
}

REMINDER = ("Private raw material for a post you write yourself. Nothing here "
            "is publishable as-is.")

NO_NOTES = "no notes yet"


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter dict, body without the block).

    Deliberately a flat scalar/list reader rather than a YAML parse: the pack
    only needs `track`, and a topic file with an odd block should still make a
    pack instead of raising.
    """
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    key = None
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) or line.lstrip().startswith("- "):
            item = line.strip().lstrip("-").strip().strip("'\"")
            if key and item:
                meta.setdefault(key, [])
                if isinstance(meta[key], list):
                    meta[key].append(item)
            continue
        k, sep, v = line.partition(":")
        if not sep:
            continue
        key = k.strip()
        v = v.strip().strip("'\"")
        meta[key] = v if v else []
    return meta, text[m.end():]


def track_for(slug: str, meta: dict | None = None) -> str:
    """Track from frontmatter when the routine wrote one, else the slug."""
    if meta:
        declared = meta.get("track")
        if isinstance(declared, str) and declared.strip():
            return declared.strip()
    for prefix in sorted(TRACK_PREFIXES, key=len, reverse=True):
        if slug.startswith(prefix):
            return TRACK_PREFIXES[prefix]
    return "unknown"


def title_for(slug: str, body: str) -> str:
    """The H1 is the title; a topic without one falls back to its slug."""
    m = _H1.search(body)
    if m:
        return m.group(1).strip()
    return slug.replace("-", " ").title()


def _norm(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def order_notes(notes: list[dict], body: str) -> list[dict]:
    """Highlights in the order they appear in the article.

    Quotes that no longer match anything in the body keep their creation
    order and go last, so a note is never silently dropped.
    """
    haystack = _norm(body).lower()
    ordered = []
    for i, note in enumerate(notes or []):
        quote = _norm(note.get("quote", "")).lower()
        pos = haystack.find(quote) if quote else -1
        ordered.append(((1, i, i) if pos < 0 else (0, pos, i), note))
    return [n for _, n in sorted(ordered, key=lambda pair: pair[0])]


_DATED_HEADING = re.compile(r"^##\s+\d{4}-\d{2}-\d{2}\b", re.M)


def session_sections(text: str) -> list[str]:
    """Split session-notes.md into its dated `## YYYY-MM-DD ...` sections.

    Anything before the first dated heading (the file's own H1 and blurb) is
    not a session and is dropped.
    """
    starts = [m.start() for m in _DATED_HEADING.finditer(text or "")]
    bounds = starts + [len(text or "")]
    return [text[bounds[i]:bounds[i + 1]].strip()
            for i in range(len(starts))]


_TITLE_PREFIX = re.compile(r"^[A-Za-z][\w /.#+-]{0,24}:\s*")


def session_needles(slug: str, title: str) -> list[str]:
    """What counts as "this section mentions this topic".

    The slug and the H1 title, plus the title with its track prefix removed:
    topic H1s are written `DSA: sliding window & two pointers` while the
    session heading for the same evening is `Sliding window & two pointers
    (taught from scratch)`, so the bare title never matches on its own.
    """
    needles = [slug, title]
    if title:
        stripped = _TITLE_PREFIX.sub("", title).strip()
        if stripped and stripped != title:
            needles.append(stripped)
    return [n.lower() for n in needles if n and n.strip()]


def matching_sessions(text: str, slug: str, title: str) -> list[str]:
    """Sections that mention this topic by slug or by title."""
    needles = session_needles(slug, title)
    out = []
    for section in session_sections(text):
        low = section.lower()
        if any(n in low for n in needles):
            out.append(section)
    return out


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def build_pack(study_dir: Path, conn, slug: str) -> str:
    """Assemble the markdown pack for one topic."""
    from . import db as _db

    study_dir = Path(study_dir)
    topic_file = study_dir / "topics" / f"{slug}.md"
    if not topic_file.exists():
        raise FileNotFoundError(
            f"no topic file for '{slug}': expected {topic_file}")

    raw = topic_file.read_text(encoding="utf-8")
    meta, body = split_frontmatter(raw)
    body = body.strip()
    title = title_for(slug, body)
    track = track_for(slug, meta)

    out = [
        f"# {title}",
        "",
        f"track: {track} · slug: {slug} · generated: {date.today().isoformat()}",
        "",
        REMINDER,
        "",
        body,
        "",
    ]

    deck = _read(study_dir / "cards" / f"{slug}.md")
    if deck is not None:
        out += ["## Flashcards", "", deck.strip(), ""]

    diagram = study_dir / "diagrams" / f"{slug}.svg"
    if diagram.exists():
        out += ["## Diagram", "", f"diagrams/{slug}.svg", ""]

    out += ["## Your highlights and notes", ""]
    notes = order_notes(_db.study_notes(conn, slug), body)
    if not notes:
        out += [NO_NOTES, ""]
    for note in notes:
        quote = _norm(note.get("quote", ""))
        out += [f"> {quote}", ""]
        out += [(note.get("note") or "").strip() or "(no note)", ""]

    sessions = _read(study_dir / "session-notes.md")
    if sessions:
        matched = matching_sessions(sessions, slug, title)
        if matched:
            out += ["## Session notes", ""]
            for section in matched:
                out += [section, ""]

    return "\n".join(out).rstrip() + "\n"


def write_pack(study_dir: Path, conn, slug: str) -> Path:
    """Write the pack to `<study_dir>/packs/<slug>.md` and return the path."""
    text = build_pack(study_dir, conn, slug)
    out_dir = Path(study_dir) / "packs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{slug}.md"
    path.write_text(text, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    from . import db as _db
    from . import settings

    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        print("usage: python -m jobscout.pack <slug>", file=sys.stderr)
        return 1
    slug = argv[0].removesuffix(".md")
    try:
        path = write_pack(settings.study_dir(), _db.connect(), slug)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
