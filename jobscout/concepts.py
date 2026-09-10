"""The concept layer: what each topic teaches, and what it needs first.

Two more keys in the frontmatter the daily routine already maintains:

    ---
    track: backend
    concepts: [covering index, execution plan, change tracking]
    prereqs: [sql-indexing-query-performance]
    ---

`concepts` is the curated 5 to 10 canonical names a topic teaches;
`prereqs` is the 0 to 3 topics you want under your belt before this one.
Resume drills carry neither. Everything visible - the concept sheet, the
worksheet order, the chips on a topic page - is a function of those two
lists, so nothing here is ever hand-maintained twice.

Names drift ("KV cache", "KV-cache paging", "paged KV cache" are one idea
spelled three ways), so a name is compared through `normalise`: lowercase,
split, stop words dropped, each token stemmed, sorted. Two names that
normalise to the same tuple ARE the same concept and get one entry with the
first spelling seen as its canonical form. Names that normalise to nearly
the same tuple are a lint warning, not a merge - guessing there would quietly
lose a distinction you meant to draw.

Prerequisites are topic-level and directed: `prereqs[slug]` is what `slug`
needs first. The routine proposes them and rewrites topic files daily, so
your corrections live in the DB (`study_prereq_overrides`) instead, and the
effective set is frontmatter minus the removes plus the adds. A cycle in the
result is broken deterministically rather than raised: an unreadable order
is worse than a slightly wrong one, and `lint` names the edge it dropped.

`build` never lints. The dashboard calls it on every render (behind a cache)
and wants the graph; `lint` is for the CLI and the routine.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

from jobscout import db, graph, settings

# A deck-sized list: enough to cover the topic, few enough to be curated.
MIN_CONCEPTS = 5
MAX_CONCEPTS = 10

# A topic nobody has studied for this long stops waiting for its
# prerequisites. Without it the DAG can starve a leaf forever.
STARVATION_DAYS = 14

# The generated sheet, written by `python -m jobscout.concepts sheet` (WS-C).
SHEET_NAME = "CONCEPTS.md"

# Words that carry no meaning in a concept name. "vs" and "versus" go too:
# "batching vs streaming" and "streaming vs batching" are one comparison.
_STOP_WORDS = frozenset({"a", "an", "the", "of", "and", "vs", "versus"})

# A suffix stripper, not a stemmer library: this project takes no dependency
# it can write in fifteen lines. The pairs it has to get right are pinned in
# tests/unit/test_concepts.py - "tracking"/"tracker" and "paging"/"paged"
# have to land on one stem, "index"/"indexes" too, and "cache"/"caches" must
# not land on two (hence the trailing "e", which also keeps "change" and
# "changed" together).
#
# Each suffix carries the shortest stem it may leave behind. "er" wants one
# letter more than the rest so that "tracker" reduces to "track" while
# "cover" is left alone rather than shaved down to "cov". Two passes,
# because a plural agent noun wears two suffixes at once: trackers -> s ->
# tracker -> er -> track.
_SUFFIXES = (("ing", 3), ("ed", 3), ("er", 4), ("es", 3), ("s", 3))
_PASSES = 2
_MIN_STEM = 3

_WORD = re.compile(r"[a-z0-9]+")

# A bold name in the generated sheet: "- **covering index** — …".
_SHEET_NAME_RE = re.compile(r"\*\*([^*]+)\*\*")


# ── names ───────────────────────────────────────────────────────────

def _stem(token: str) -> str:
    for _ in range(_PASSES):
        for suffix, floor in _SUFFIXES:
            if token.endswith(suffix) and len(token) - len(suffix) >= floor:
                token = token[:-len(suffix)]
                break
        else:
            break
    if len(token) > _MIN_STEM and token.endswith("e"):
        token = token[:-1]
    return token


def normalise(name: str) -> tuple[str, ...]:
    """A concept name reduced to its comparable form: the sorted tuple of its
    stemmed, non-stop-word tokens.

    "KV-cache paging", "paged KV cache" and "Paging the KV Caches" all come
    back as ("cach", "kv", "pag"). A name that is nothing but stop words
    comes back empty and is not a concept.
    """
    tokens = [_stem(t) for t in _WORD.findall((name or "").lower())
              if t not in _STOP_WORDS]
    return tuple(sorted(t for t in tokens if t))


def concept_id(key: tuple[str, ...]) -> str:
    """The namespaced id a concept carries wherever it meets topic slugs -
    on the map, in a URL, in an edge list. Topic slugs never contain a colon,
    so `c:` is enough to tell the two apart."""
    return "c:" + " ".join(key)


def near_duplicate(a: str, b: str) -> bool:
    """Whether two concept names are close enough to be worth a second look.

    Two names are near-duplicates when their normalised token sets are equal,
    or when one is a strict subset of the other, the sets differ by a single
    token, and the smaller one has at least two tokens of its own.

    That last clause is what keeps the rule useful: "covering index" and
    "index" differ by one token too, but the smaller name is a single word
    and single words are the general case a specific name is built on.
    "continuous batching" versus "batching" is the same shape. Both are
    real distinctions and neither is flagged.
    """
    ka, kb = set(normalise(a)), set(normalise(b))
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    small, large = (ka, kb) if len(ka) <= len(kb) else (kb, ka)
    return (small < large
            and len(large) - len(small) <= 1
            and len(small) >= 2)


# ── frontmatter ─────────────────────────────────────────────────────

def _names(value) -> list[str]:
    """`concepts` as written, in order, without repeats within one topic."""
    out: list[str] = []
    for name in graph._as_str_list(value):
        if name not in out:
            out.append(name)
    return out


def _slugs(value) -> list[str]:
    """`prereqs` as bare slugs. The same spellings `related` accepts, so the
    routine can write the two lists the same way."""
    out: list[str] = []
    for slug in graph._as_slug_list(value):
        if slug not in out:
            out.append(slug)
    return out


def _as_date(value) -> date | None:
    """A frontmatter date, which YAML hands over as a date, a datetime or a
    string depending on how it was written."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (ValueError, TypeError):
        return None


# ── prerequisites ───────────────────────────────────────────────────

def _overrides(conn) -> dict:
    """Your corrections, or none at all. A DB that is missing, locked or
    older than the table costs you the overrides, not the graph."""
    if conn is None:
        return {}
    try:
        return db.overrides_map(conn)
    except sqlite3.Error:
        return {}


def _find_cycle(out_edges: dict) -> list[str] | None:
    """One cycle as the list of slugs around it, or None. Deterministic:
    sources and targets are both walked in slug order, so the same folder
    always yields the same cycle first."""
    colour: dict = {}
    stack: list[str] = []

    def visit(node):
        colour[node] = 1
        stack.append(node)
        for nxt in out_edges.get(node, ()):
            if colour.get(nxt, 0) == 0:
                found = visit(nxt)
                if found:
                    return found
            elif colour[nxt] == 1:
                return stack[stack.index(nxt):]
        stack.pop()
        colour[node] = 2
        return None

    for node in sorted(out_edges):
        if colour.get(node, 0) == 0:
            found = visit(node)
            if found:
                return found
    return None


def _break_cycles(prereqs: dict, topics: dict) -> list[dict]:
    """Cut every cycle out of the prerequisite graph, newest declaration
    first, and report what was cut.

    The edge dropped is the one whose SOURCE topic was updated last, ties
    broken by slug: the most recently rewritten file is the one most likely
    to have had a prerequisite invented for it. Deterministic, so the same
    folder always loses the same edge and the order on the page does not
    shuffle between renders.
    """
    cycles: list[dict] = []
    while True:
        found = _find_cycle(prereqs)
        if not found:
            return cycles
        pairs = [(found[i], found[(i + 1) % len(found)])
                 for i in range(len(found))]
        source, target = max(
            pairs, key=lambda p: (str(topics.get(p[0], {}).get("updated", "")), p[0]))
        prereqs[source] = [p for p in prereqs[source] if p != target]
        cycles.append({"cycle": list(found), "source": source, "target": target})


def would_cycle(result: dict, slug: str, prereq: str) -> bool:
    """Whether making `prereq` a prerequisite of `slug` would close a loop.

    The guard behind the "add a prerequisite" picker on the topic page: a
    topic may not require anything that already requires it, directly or at
    any remove, and may not require itself.
    """
    if slug == prereq:
        return True
    prereqs = result["prereqs"]
    seen, queue = set(), [prereq]
    while queue:
        node = queue.pop()
        if node == slug:
            return True
        if node in seen:
            continue
        seen.add(node)
        queue.extend(prereqs.get(node, ()))
    return False


# ── the concept graph ───────────────────────────────────────────────

def build(study_dir: Path, conn=None) -> dict:
    """The whole concept layer, as one dict:

        topics    slug -> the graph node plus this file's `concepts` and
                  declared `prereqs`, its `created`/`updated` stamps, its
                  path and its mtime
        concepts  normalised key -> {id, canonical, names, topics}
        edges     [{source: slug, target: "c:…", kind: "concept"}]
        prereqs   slug -> effective prerequisites, overrides applied and
                  cycles cut
        cycles    [{cycle: [...], source, target}] - what was cut and why
        overrides slug -> {prereq: "add" | "remove"} as recorded
        unresolved slug -> prerequisite slugs with no file, dropped from
                  `prereqs` the way graph.build drops a dangling `related`
        graph     the link graph this was built on, so a caller that wants
                  both does not walk the folder twice

    Never lints and never raises on a bad file: an unreadable topic
    contributes no concepts and no prerequisites.
    """
    linked = graph.build(study_dir, conn)
    nodes = {n["slug"]: n for n in linked["nodes"]}
    overrides = _overrides(conn)

    topics: dict = {}
    for f in graph.topic_files(study_dir):
        slug = f.stem
        try:
            meta, _ = graph.split_frontmatter(f.read_text())
            mtime = f.stat().st_mtime
        except OSError:
            meta, mtime = {}, 0.0
        node = dict(nodes.get(slug) or {"slug": slug, "title": slug,
                                        "track": graph.track_of(slug)})
        node.update({
            "concepts": _names(meta.get("concepts")),
            "prereqs": _slugs(meta.get("prereqs")),
            "created": _as_date(meta.get("created")),
            "updated": str(meta.get("updated") or ""),
            "path": f,
            "mtime": mtime,
        })
        topics[slug] = node

    # concepts, in slug order so "first spelling seen" is a fact about the
    # folder rather than about the filesystem's iteration order
    concepts: dict = {}
    edges: list[dict] = []
    seen_edges: set = set()
    for slug in sorted(topics):
        for name in topics[slug]["concepts"]:
            key = normalise(name)
            if not key:
                continue                       # a name that is all stop words
            entry = concepts.get(key)
            if entry is None:
                entry = concepts[key] = {"id": concept_id(key), "canonical": name,
                                         "names": [], "topics": []}
            if name not in entry["names"]:
                entry["names"].append(name)
            if slug not in entry["topics"]:
                entry["topics"].append(slug)
            pair = (slug, entry["id"])
            if pair not in seen_edges:
                seen_edges.add(pair)
                edges.append({"source": slug, "target": entry["id"],
                              "kind": "concept"})

    # effective prerequisites: frontmatter, minus your removes, plus your adds
    prereqs: dict = {}
    unresolved: dict = {}
    for slug in sorted(topics):
        mine = overrides.get(slug, {})
        wanted = [p for p in topics[slug]["prereqs"] if mine.get(p) != "remove"]
        wanted += [p for p, action in sorted(mine.items())
                   if action == "add" and p not in wanted]
        kept, missing = [], []
        for p in wanted:
            if p == slug:
                continue                       # a topic is not its own prereq
            (kept if p in topics else missing).append(p)
        prereqs[slug] = kept
        if missing:
            unresolved[slug] = missing

    cycles = _break_cycles(prereqs, topics)

    return {"topics": topics, "concepts": concepts, "edges": edges,
            "prereqs": prereqs, "cycles": cycles, "overrides": overrides,
            "unresolved": unresolved, "graph": linked}


# ── lint ────────────────────────────────────────────────────────────

def lint(result: dict) -> list[str]:
    """Everything worth a second look about the concept layer, as lines.

    A report, never a gate - same contract as `graph check` and `cards`. The
    routine runs it at the end of a run and the new names go in the brief;
    nothing downstream needs a clean report to work.
    """
    topics, prereqs = result["topics"], result["prereqs"]
    out: list[str] = []

    for slug in sorted(topics):
        topic = topics[slug]
        if topic["track"] == "resume":
            continue                           # drills teach a story, not concepts
        names = topic["concepts"]
        if not names:
            out.append(f"{slug}: no concepts in frontmatter")
        elif len(names) < MIN_CONCEPTS or len(names) > MAX_CONCEPTS:
            out.append(f"{slug}: {len(names)} concept(s); a topic wants "
                       f"{MIN_CONCEPTS} to {MAX_CONCEPTS}")

    for slug in sorted(result["unresolved"]):
        for prereq in result["unresolved"][slug]:
            out.append(f"{slug}: prereq '{prereq}' has no file")

    for slug in sorted(prereqs):
        for prereq in prereqs[slug]:
            if topics[prereq]["track"] == "resume":
                out.append(f"{slug}: prereq '{prereq}' is a resume drill; "
                           f"drills unlock nothing")

    for cut in result["cycles"]:
        loop = " -> ".join(cut["cycle"] + cut["cycle"][:1])
        out.append(f"cycle {loop}: dropped '{cut['target']}' from "
                   f"{cut['source']}'s prereqs")

    out.extend(_near_duplicate_warnings(result["concepts"]))
    return out


def _near_duplicate_warnings(concepts: dict) -> list[str]:
    """One line per pair of names close enough to be the same idea. Pairs are
    walked in key order so the report reads the same twice running."""
    out: list[str] = []
    keys = sorted(concepts)

    for key in keys:                           # one concept, several spellings
        names = concepts[key]["names"]
        where = concepts[key]["topics"]
        for i, first in enumerate(names):
            for second in names[i + 1:]:
                out.append(_duplicate_line(first, second, where, where))

    for i, key in enumerate(keys):             # two concepts, nearly one name
        for other in keys[i + 1:]:
            a, b = concepts[key], concepts[other]
            if near_duplicate(a["canonical"], b["canonical"]):
                out.append(_duplicate_line(a["canonical"], b["canonical"],
                                           a["topics"], b["topics"]))
    return out


def _duplicate_line(first: str, second: str, here: list, there: list) -> str:
    where = ", ".join(sorted(set(here) | set(there)))
    return f"near-duplicate concepts: '{first}' ~ '{second}' (in {where})"


# ── the daily diff of new names ─────────────────────────────────────

def sheet_path(study_dir: Path) -> Path:
    return Path(study_dir) / SHEET_NAME


def new_names(result: dict, sheet_path) -> list[str]:
    """Concept names in the study base that the sheet on disk has never
    heard of, for the brief to list.

    Two names a day is a review you actually do; a hundred names once is a
    review nobody does. Comparison is by normalised key, so respelling an
    existing concept is not a new name - the near-duplicate lint is what
    catches that.
    """
    try:
        text = Path(sheet_path).read_text()
    except OSError:
        text = ""                              # no sheet yet: everything is new
    known = {normalise(m.group(1)) for m in _SHEET_NAME_RE.finditer(text)}
    known.discard(())
    return sorted((entry["canonical"] for key, entry in result["concepts"].items()
                   if key not in known), key=str.lower)


# ── study order ─────────────────────────────────────────────────────

def _studied_map(result: dict, conn) -> dict:
    """slug -> studied. The build may be cached against the topic folder, and
    ticking a checkbox does not touch the folder, so when there is a
    connection the ticks are re-read here rather than trusted from the node.
    """
    topics = result["topics"]
    if conn is None:
        return {slug: bool(t.get("studied")) for slug, t in topics.items()}
    try:
        progress = db.study_progress_map(conn)
    except sqlite3.Error:
        return {slug: bool(t.get("studied")) for slug, t in topics.items()}
    return {slug: graph.is_studied(progress.get(slug), t["path"])
            for slug, t in topics.items()}


def _pending(slug: str, prereqs: dict, studied: dict) -> list[str]:
    """Every unstudied topic behind this one, however far back.

    The walk goes THROUGH a studied prerequisite rather than stopping at it,
    which is the whole answer to the a -> b -> c question: with b studied, c
    is ready to be read today, and still owes you a. Counting these is also
    what orders the rest sensibly when nothing at all is ready - the fewer
    you owe, the closer you are.
    """
    found: set = set()
    seen: set = {slug}
    queue = list(prereqs.get(slug, ()))
    while queue:
        node = queue.pop()
        if node in seen:
            continue
        seen.add(node)
        if not studied.get(node):
            found.add(node)
        queue.extend(prereqs.get(node, ()))
    return sorted(found)


def _days_unstudied(topic: dict, today: date) -> int:
    created = topic.get("created")
    if created is None:
        created = date.fromtimestamp(topic.get("mtime") or 0) if topic.get("mtime") \
            else today
    return (today - created).days


def study_order(result: dict, conn=None, now=None) -> dict:
    """What to study next: {"order": [row, …], "studied": [slug, …]}.

    Technical topics only, studied ones lifted out and listed separately.
    A row is {slug, title, ready, promoted, after, blocked_by, pending,
    mtime, reason}. The order is ready first, then whoever owes the fewest
    topics behind them, then oldest debt first by mtime, then slug.

    Ready means every effective prerequisite is studied - or that the topic
    has sat unstudied for more than STARVATION_DAYS days, which is the
    escape hatch that stops a leaf of the DAG waiting forever.
    """
    today = now.date() if isinstance(now, datetime) else (now or date.today())
    topics, prereqs = result["topics"], result["prereqs"]
    studied = _studied_map(result, conn)

    rows: list[dict] = []
    done: list[str] = []
    for slug in sorted(topics):
        topic = topics[slug]
        if topic["track"] == "resume":
            continue                           # drills are drilled, not ordered
        if studied.get(slug):
            done.append(slug)
            continue
        blocked_by = [p for p in prereqs.get(slug, ()) if not studied.get(p)]
        after = _pending(slug, prereqs, studied)
        promoted = bool(blocked_by) and _days_unstudied(topic, today) > STARVATION_DAYS
        ready = not blocked_by or promoted
        if promoted:
            reason = (f"promoted; unstudied for over {STARVATION_DAYS} days")
        elif ready:
            reason = "ready"
        else:
            reason = "after " + ", ".join(after)
        rows.append({"slug": slug, "title": topic.get("title", slug),
                     "ready": ready, "promoted": promoted, "after": after,
                     "blocked_by": blocked_by, "pending": len(after),
                     "mtime": topic["mtime"], "reason": reason})

    rows.sort(key=lambda r: (not r["ready"], r["pending"], r["mtime"], r["slug"]))
    return {"order": rows, "studied": done}


# ── WS-C lands here ─────────────────────────────────────────────────
# `definition_for(name, deck)` - the answer of the first definitional card
# mentioning the name - and `sheet(result, …)` writing study/CONCEPTS.md,
# plus the `sheet` command in the CLI below, belong in this section. Nothing
# above needs them: `new_names` reads whatever sheet it is handed, and
# `sheet_path` already knows where that is.


# ── CLI ─────────────────────────────────────────────────────────────

def _open_db():
    if not Path(db.DB_PATH).exists():          # never create one here
        return None
    try:
        return db.connect()
    except sqlite3.Error:
        return None                            # the files still have plenty to say


def _cli(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd not in ("check", "order"):
        print(f"Unknown command '{cmd}'. Use: check | order")
        return 0

    study = settings.study_dir()
    conn = _open_db()
    try:
        result = build(study, conn)

        if cmd == "check":
            warnings = lint(result)
            print(f"{len(result['topics'])} topics, "
                  f"{len(result['concepts'])} concepts, "
                  f"{len(warnings)} warning(s)")
            for line in warnings:
                print(f"  {line}")
            fresh = new_names(result, sheet_path(study))
            if fresh:
                print(f"\n{len(fresh)} concept name(s) not yet on the sheet:")
                for name in fresh:
                    print(f"  {name}")
            else:
                print("\nNo new concept names since the last sheet.")
            return 0

        order = study_order(result, conn)
        print(f"{len(order['order'])} topic(s) to study, "
              f"{len(order['studied'])} studied")
        for i, row in enumerate(order["order"], 1):
            print(f"  {i}. {row['slug']} ({row['reason']})")
        return 0
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
