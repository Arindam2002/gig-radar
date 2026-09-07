"""The study link graph: topic frontmatter, the edges between topics, and
the health warnings that keep the graph worth drawing.

Two sources of truth, one edge set. A topic file carries a small YAML
frontmatter block the daily routine maintains:

    ---
    track: llm-infra
    tags: [rag, retrieval]
    related: [llm-evaluation-and-guardrails, sql-indexing-query-performance]
    created: 2026-09-07
    updated: 2026-09-07
    ---

`related` is the deliberate "these belong together" list; the inline
`[text](<slug>.md)` links the routine drops into an answer are the
incidental ones. Both become edges, `related` wins when a pair appears in
both, and everything downstream (the topic page's Related strip, and later
the map and the export) reads this one graph.

Nothing here writes to a topic file except `repair`, which only ever
prepends a missing block and restores the file's mtime afterwards. mtime is
load-bearing across this project: the dashboard, the worksheet order and the
routine all decide "studied" by comparing it with study_progress.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

from jobscout import db, settings

# Same link shapes the dashboard rewrites into topic-page links: bare
# "<slug>.md" between siblings, "topics/<slug>.md" from STUDY.md, and
# "../study/topics/<slug>.md" from a brief.
TOPIC_MD_LINK = re.compile(
    r"\[([^\]]+)\]\((?:\.\./)?(?:study/)?(?:topics/)?(?:\./)?"
    r"([a-zA-Z0-9\-_]+)\.md(?:#[^)]*)?\)")

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)

_H1 = re.compile(r"^#[ \t]+(.+?)[ \t]*$", re.M)

# slug prefix -> track, longest prefix first so "system-design-" is not
# shadowed by a shorter neighbour later on
_TRACK_BY_PREFIX = (
    ("system-design-", "system-design"),
    ("agentic-", "llm-infra"),
    ("resume-", "resume"),
    ("dotnet-", "backend"),
    ("dsa-", "dsa"),
    ("llm-", "llm-infra"),
    ("rag-", "llm-infra"),
    ("sql-", "backend"),
    ("api-", "backend"),
)

MIN_RELATED = 2


def split_frontmatter(text: str) -> tuple[dict, str]:
    """(metadata, body) for a file that opens with a YAML frontmatter block.

    Anything that is not a clean leading block of YAML mapping is treated as
    "no frontmatter": the caller gets an empty dict and the text untouched.
    A malformed block must never take the reader down, so nothing raises.
    """
    text = text or ""
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, text[m.end():]


def track_of(slug: str, meta: dict | None = None) -> str:
    """The topic's track: what the frontmatter says, else what the slug
    prefix implies, else "unknown"."""
    declared = (meta or {}).get("track")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    for prefix, track in _TRACK_BY_PREFIX:
        if slug.startswith(prefix):
            return track
    return "unknown"


def title_of(body: str, slug: str) -> str:
    """The first `# ` heading, or the slug read out loud."""
    m = _H1.search(body or "")
    if m:
        return m.group(1).strip()
    return slug.replace("-", " ").title()


def is_studied(completed_at: str | None, path: Path) -> bool:
    """Whether a topic counts as studied: ticked off, and not rewritten
    since. THE comparison - the dashboard, the worksheet order and the
    routine's rule 7 all mean this one."""
    if not completed_at:
        return False
    try:
        dt = datetime.fromisoformat(completed_at)
    except (ValueError, TypeError):
        return False
    try:
        return dt.timestamp() >= path.stat().st_mtime
    except OSError:
        return False


def topics_dir(study_dir: Path) -> Path:
    return Path(study_dir) / "topics"


def topic_files(study_dir: Path) -> list[Path]:
    d = topics_dir(study_dir)
    return sorted(d.glob("*.md")) if d.is_dir() else []


def _as_str_list(value) -> list[str]:
    """A frontmatter list, tolerating a bare string and a missing key."""
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return []
    return [s for s in (str(item).strip() for item in items) if s]


def _as_slug_list(value) -> list[str]:
    """`related` entries as bare slugs: people write `foo`, `foo.md` and
    `topics/foo.md` and all three mean the same topic."""
    out = []
    for s in _as_str_list(value):
        if s.endswith(".md"):
            s = s[:-3]
        s = s.strip("/").split("/")[-1]
        if s:
            out.append(s)
    return out


def _reading_state(conn) -> tuple[dict, dict]:
    """(slug -> completed_at, slug -> note count). The graph is a picture of
    the files first; a DB that is missing, locked or older than these tables
    costs you the studied rings, not the graph."""
    if conn is None:
        return {}, {}
    try:
        return db.study_progress_map(conn), db.study_note_counts(conn)
    except sqlite3.Error:
        return {}, {}


def build(study_dir: Path, conn=None) -> dict:
    """The whole graph: {"nodes": [...], "edges": [...], "warnings": [...]}.

    A node is {slug, title, track, tags, words, studied, notes}; an edge is
    {source, target, kind} with kind "related" (declared in frontmatter) or
    "link" (an inline link in the body). Edges to slugs with no file are
    dropped - reported as a warning when they were declared, silently when
    they were an inline link to something that is not a topic.
    """
    files = topic_files(study_dir)
    known = {f.stem: f for f in files}
    progress, note_counts = _reading_state(conn)

    nodes: list[dict] = []
    warnings: list[str] = []
    related_pairs: list[dict] = []
    link_pairs: list[dict] = []

    for f in files:
        slug = f.stem
        meta, body = split_frontmatter(f.read_text())
        track = track_of(slug, meta)
        nodes.append({
            "slug": slug,
            "title": title_of(body, slug),
            "track": track,
            "tags": _as_str_list(meta.get("tags")),
            "words": len(body.split()),
            "studied": is_studied(progress.get(slug), f),
            "notes": int(note_counts.get(slug, 0)),
        })

        if not meta:
            warnings.append(f"{slug}: no frontmatter block")

        resolved = 0
        for target in _as_slug_list(meta.get("related")):
            if target == slug:
                continue
            if target not in known:
                warnings.append(f"{slug}: related '{target}' has no file")
                continue
            resolved += 1
            related_pairs.append({"source": slug, "target": target, "kind": "related"})

        if track != "resume" and resolved < MIN_RELATED:
            warnings.append(
                f"{slug}: {resolved} related topic(s); technical topics want "
                f"at least {MIN_RELATED}")

        for m in TOPIC_MD_LINK.finditer(body):
            target = m.group(2)
            if target == slug or target not in known:
                continue
            link_pairs.append({"source": slug, "target": target, "kind": "link"})

    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for e in related_pairs + link_pairs:   # "related" wins the duplicate
        key = (e["source"], e["target"])
        if key in seen:
            continue
        seen.add(key)
        edges.append(e)

    return {"nodes": nodes, "edges": edges, "warnings": warnings}


def node_map(graph: dict) -> dict:
    return {n["slug"]: n for n in graph["nodes"]}


def neighbors(graph: dict, slug: str) -> list[dict]:
    """Every topic this one points at, plus every topic pointing back at it,
    as nodes sorted by title. Direction is for the routine to reason about;
    a reader just wants the neighbourhood."""
    by_slug = node_map(graph)
    found = set()
    for e in graph["edges"]:
        if e["source"] == slug:
            found.add(e["target"])
        elif e["target"] == slug:
            found.add(e["source"])
    found.discard(slug)
    return sorted((by_slug[s] for s in found if s in by_slug),
                  key=lambda n: (n["title"].lower(), n["slug"]))


def _minimal_block(slug: str, when: date) -> str:
    stamp = when.isoformat()
    return ("---\n"
            f"track: {track_of(slug)}\n"
            "tags: []\n"
            "related: []\n"
            f"created: {stamp}\n"
            f"updated: {stamp}\n"
            "---\n\n")


def repair(study_dir: Path) -> list[str]:
    """Give every topic missing a frontmatter block a minimal one, guessing
    the track from the slug and dating it from the file itself. The file's
    mtime is restored afterwards: adding metadata is not deepening a topic,
    and half this project reads mtime as "when it last changed for you"."""
    repaired = []
    for f in topic_files(study_dir):
        text = f.read_text()
        meta, _ = split_frontmatter(text)
        if meta:
            continue
        st = f.stat()
        block = _minimal_block(f.stem, date.fromtimestamp(st.st_mtime))
        f.write_text(block + text.lstrip("\n"))
        os.utime(f, (st.st_atime, st.st_mtime))
        repaired.append(f.stem)
    return repaired


def _cli(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    study = settings.study_dir()
    if cmd == "repair":
        done = repair(study)
        if done:
            print(f"Added a frontmatter block to {len(done)} topic(s):")
            for slug in done:
                print(f"  {slug}")
            print("mtimes restored; nothing counts as deepened.")
        else:
            print("Every topic already has frontmatter. Nothing to do.")
        return 0
    if cmd != "check":
        print(f"Unknown command '{cmd}'. Use: check | repair")
        return 0
    conn = None
    if Path(db.DB_PATH).exists():                       # never create one here
        try:
            conn = db.connect()
        except sqlite3.Error:
            pass                                        # warnings need no DB
    try:
        graph = build(study, conn)
    finally:
        if conn is not None:
            conn.close()
    print(f"{len(graph['nodes'])} topics, {len(graph['edges'])} edges, "
          f"{len(graph['warnings'])} warning(s)")
    for w in graph["warnings"]:
        print(f"  {w}")
    return 0                                            # a report, not a gate


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
