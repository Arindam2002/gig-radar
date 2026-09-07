"""Export the topics you have reviewed into the Quartz archive site.

The study folder is private and the routine rewrites it every day. The
archive site is public and permanent. This module is the one-way door
between them, and it is deliberately paranoid about three things.

**Version, not topic.** `study_publish` stores the sha256 of the file text
you had in front of you when you ticked "Publish this version". A topic the
routine deepened overnight no longer matches that hash, so the export holds
it back and tells you to re-read it. There is no way to publish prose you
have not read; that rule, not any word list, is the guarantee.

**Resume drills never leave.** They quote your own claims back at you and
read like an interrogation transcript. They are excluded by track, and again
by their markers ("Interviewer probes", "The claim", "[FILL:") and by any
link pointing at a `resume-` slug. Those are hard refusals: the file is not
written and the command exits non-zero.

**Company names only warn.** The list is built at run time from the
`companies` and `jobs` tables, matched on word boundaries, with short names
and a stoplist of ordinary English words and of vendors you legitimately
discuss in llm-infra topics dropped. It is a heuristic prompt to re-read a
paragraph, never a gate, and no denylist is stored in this repo.

What lands in `<out>` is a Quartz-flavoured copy of the topic: frontmatter
rewritten to `title`/`tags`/`date`, the H1 dropped (Quartz renders the
title), the "Deepen next time" and dated "Deepened" sections stripped, inline
topic links turned into `[[wikilinks]]` when their target is also being
exported and degraded to plain text when it is not, a "Related" section of
wikilinks so the Quartz graph has real edges, the flashcard deck as
collapsible callouts, and the overview diagram if there is one.

`<out>/.published.yaml` is the manifest. Anything it listed last time and
does not list now is deleted, which is what makes unpublishing real. Nothing
else under `<out>` is ever touched, so the hand-written `blog/` section and
`index.md` are safe.

    python -m jobscout.publish check
    python -m jobscout.publish export ../archive/content/study
"""
from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from jobscout import cards, db, graph, settings

MANIFEST = ".published.yaml"

# A slug is a file name and nothing else. Anything from a manifest on disk is
# checked against this before it is turned into a path to delete.
_SAFE_SLUG = re.compile(r"\A[A-Za-z0-9_-]+\Z")

_H1_LINE = re.compile(r"^#[ \t]+.+?[ \t]*$", re.M)

# The routine's own private sections. "Deepen next time" is a to-do list it
# writes for itself; the dated "Deepened"/"Answered follow-ups" sections are
# revision logs. Neither is worth reading as an article.
_DROP_SECTION = re.compile(
    r"^##[ \t]+(?:Deepen next time|Deepened\b|Answered follow-ups\b)",
    re.I)
_ANY_H2 = re.compile(r"^##[ \t]+")
_ANY_H1 = re.compile(r"^#[ \t]+")

# Markdown links and Obsidian wikilinks in the rendered output, so the leak
# check can look at what a link says AND where it points.
_MD_LINK = re.compile(r"\[([^\]\[]*)\]\(([^)\s]+)[^)]*\)")
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")

# Refusals: the shapes a resume drill takes. A technical topic never contains
# these, so a hit means a drill (or a paragraph quoting one) reached the door.
MARKERS = ("Interviewer probes", "The claim", "[FILL:")

RESUME_PREFIX = "resume-"

# Names that are companies in the DB and also ordinary words, plus the
# vendors an llm-infra topic is supposed to name. Matching these would make
# every warning list noise, and noise is how a real hit gets ignored.
STOPLIST = {
    "nvidia", "openai", "anthropic", "microsoft", "oracle", "cloudflare",
    "cohere", "meta", "sap", "google", "amazon", "redis", "develop",
    "target", "magic", "salt", "raft", "pipe", "slate", "stitch", "twenty",
    "circle", "world", "insight", "harness", "slice", "super", "clay",
    "bolt", "ford",
}

# Shorter than this and a company name is a substring of ordinary prose far
# more often than it is a company.
MIN_COMPANY_LEN = 5


def content_hash(text: str) -> str:
    """The sha256 of the topic file exactly as it sits on disk, frontmatter
    included. The dashboard stores this when you approve a version and the
    export compares it; both must hash the same bytes, so neither strips
    anything first."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ── the leak check ──────────────────────────────────────────────────

def company_names(conn) -> list[str]:
    """Company names worth warning about, from the pipeline itself.

    Built at run time from `companies.name` and `jobs.company` so nothing
    resembling a denylist is ever committed to this repo. Names shorter than
    MIN_COMPANY_LEN and names in STOPLIST are dropped.
    """
    if conn is None:
        return []
    names = set()
    for sql in ("SELECT name FROM companies", "SELECT company FROM jobs"):
        try:
            rows = conn.execute(sql).fetchall()
        except sqlite3.Error:
            continue
        for r in rows:
            n = (r[0] or "").strip()
            if len(n) >= MIN_COMPANY_LEN and n.lower() not in STOPLIST:
                names.add(n)
    return sorted(names)


def _company_pattern(names: list[str]):
    """One word-boundary alternation over every name, longest first so
    "Acme Analytics" wins over "Acme". Lookarounds rather than \\b because a
    name may end in a character \\b does not fence."""
    if not names:
        return None
    parts = "|".join(re.escape(n) for n in
                     sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{parts})(?!\w)", re.I)


def _links(text: str) -> list[tuple[str, str]]:
    """(link text, link target) for every markdown link and wikilink."""
    out = [(m.group(1), m.group(2)) for m in _MD_LINK.finditer(text)]
    out += [(m.group(2) or m.group(1), m.group(1))
            for m in _WIKILINK.finditer(text)]
    return out


def refusals(rendered: str) -> list[str]:
    """Why this page may not be published, or an empty list.

    Runs on the rendered page, which is what would actually go live: the
    body, the text of every link, and the target of every link.
    """
    found = []
    link_pairs = _links(rendered)
    haystacks = [rendered] + [t for t, _ in link_pairs]
    for marker in MARKERS:
        if any(marker in h for h in haystacks):
            found.append(f"resume-drill marker {marker!r}")
    for _, target in link_pairs:
        slug = target.rsplit("/", 1)[-1].removesuffix(".md")
        if slug.startswith(RESUME_PREFIX):
            found.append(f"link to the resume drill {slug!r}")
    return found


def company_hits(rendered: str, pattern) -> list[str]:
    """Company names the page mentions, deduplicated, in first-seen order."""
    if pattern is None:
        return []
    seen, out = set(), []
    for m in pattern.finditer(rendered):
        key = m.group(0).lower()
        if key not in seen:
            seen.add(key)
            out.append(m.group(0))
    return out


# ── rendering a topic for Quartz ────────────────────────────────────

def strip_private_sections(body: str) -> str:
    """The body without the routine's own working sections."""
    out, dropping = [], False
    for line in body.splitlines():
        if _DROP_SECTION.match(line):
            dropping = True
            continue
        if dropping and (_ANY_H2.match(line) or _ANY_H1.match(line)):
            dropping = False
        if not dropping:
            out.append(line)
    return "\n".join(out)


def rewrite_links(body: str, export_set: set) -> str:
    """`[text](<slug>.md)` becomes `[[slug|text]]` when the target is also
    being exported, and collapses to the plain link text when it is not.

    A wikilink to a page that does not exist is a dead end on the site, and
    a markdown link to a private file is worse than a dead end.
    """
    def sub(m):
        text, target = m.group(1), m.group(2)
        if target in export_set:
            return f"[[{target}|{text}]]"
        return text
    return graph.TOPIC_MD_LINK.sub(sub, body)


def _frontmatter(title: str, tags: list, updated) -> str:
    meta = {"title": title}
    if tags:
        meta["tags"] = tags
    if updated is not None and str(updated).strip():
        meta["date"] = updated
    dumped = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True,
                            default_flow_style=False).strip()
    return f"---\n{dumped}\n---\n"


def _related_section(meta: dict, slug: str, export_set: set,
                     titles: dict) -> str:
    seen, lines = set(), []
    for target in graph._as_slug_list(meta.get("related")):
        if target == slug or target in seen or target not in export_set:
            continue
        seen.add(target)
        lines.append(f"- [[{target}|{titles.get(target, target)}]]")
    if not lines:
        return ""
    return "## Related\n\n" + "\n".join(lines) + "\n"


def _flashcards_section(deck: list) -> str:
    """The deck as Quartz collapsible callouts: question on the fold,
    answer behind it, same "have a go first" contract as the dashboard."""
    if not deck:
        return ""
    blocks = []
    for card in deck:
        answer = "\n> ".join(str(card["a"]).splitlines()) or ""
        blocks.append(f"> [!question]- {card['q']}\n> {answer}")
    return "## Flashcards\n\n" + "\n\n".join(blocks) + "\n"


def render(study_dir: Path, slug: str, text: str, export_set: set,
           titles: dict) -> str:
    """One topic file as the markdown page Quartz will build."""
    meta, body = graph.split_frontmatter(text)
    title = graph.title_of(body, slug)
    track = graph.track_of(slug, meta)

    tags = list(graph._as_str_list(meta.get("tags")))
    if track and track not in tags:
        tags.append(track)

    body = strip_private_sections(body)
    body = _H1_LINE.sub("", body, count=1)          # Quartz renders the title
    body = rewrite_links(body, export_set)

    parts = [_frontmatter(title, tags, meta.get("updated"))]
    if diagram_path(study_dir, slug).exists():
        # bare filename on purpose: Quartz resolves links by shortest unique
        # path, and "diagrams/<slug>.svg" would fall back to the site root
        parts.append(f"\n![Overview]({slug}.svg)\n")
    parts.append("\n" + body.strip() + "\n")
    for section in (_related_section(meta, slug, export_set, titles),
                    _flashcards_section(cards.load(study_dir, slug))):
        if section:
            parts.append("\n" + section)
    return "".join(parts)


def diagram_path(study_dir: Path, slug: str) -> Path:
    return Path(study_dir) / "diagrams" / f"{slug}.svg"


# ── the export ──────────────────────────────────────────────────────

@dataclass
class Result:
    """What one run did, or would have done in check mode."""
    exported: list = field(default_factory=list)          # slugs written
    skipped: list = field(default_factory=list)           # (slug, why)
    refused: list = field(default_factory=list)           # (slug, [why, …])
    warned: list = field(default_factory=list)            # (slug, [name, …])
    deleted: list = field(default_factory=list)           # paths removed
    pages: dict = field(default_factory=dict)             # slug -> markdown

    @property
    def ok(self) -> bool:
        return not self.refused


def _candidates(study_dir: Path, rows: dict, result: Result) -> dict:
    """slug -> file text for every approved topic still fit to export.

    Three ways to fall out, all of them a skip with a reason rather than an
    error: the file is gone, the topic is a resume drill, or the file is no
    longer the version that was approved.
    """
    keep = {}
    for slug in sorted(rows):
        f = graph.topics_dir(study_dir) / f"{slug}.md"
        if not f.is_file():
            result.skipped.append((slug, "no topic file any more"))
            continue
        text = f.read_text(encoding="utf-8")
        meta, _ = graph.split_frontmatter(text)
        if graph.track_of(slug, meta) == "resume":
            result.skipped.append((slug, "resume drill; never published"))
            continue
        if content_hash(text) != (rows[slug].get("content_hash") or ""):
            result.skipped.append(
                (slug, "changed since you reviewed it; re-read and re-publish"))
            continue
        keep[slug] = text
    return keep


def _read_manifest(out_dir: Path) -> dict:
    try:
        loaded = yaml.safe_load((Path(out_dir) / MANIFEST).read_text())
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _prune(out_dir: Path, previous: dict, keep: set) -> list:
    """Delete the page and diagram of every slug the last run published and
    this one does not. Only those two paths, only for a slug that is a plain
    file name: a manifest is a file on disk and gets no more trust than one."""
    removed = []
    for slug in sorted(previous):
        if slug in keep or not _SAFE_SLUG.match(str(slug)):
            continue
        for p in (out_dir / f"{slug}.md", out_dir / "diagrams" / f"{slug}.svg"):
            if p.is_file():
                p.unlink()
                removed.append(str(p))
    return removed


def export(study_dir: Path, out_dir: Path, conn=None,
           dry_run: bool = False) -> Result:
    """Publish every approved, unchanged, non-refused topic into `out_dir`.

    Nothing is written until every page has been rendered and checked, so a
    refusal in the last topic cannot leave a half-published site behind.
    """
    study_dir, out_dir = Path(study_dir), Path(out_dir)
    result = Result()
    rows = db.publish_map(conn) if conn is not None else {}
    keep = _candidates(study_dir, rows, result)

    export_set = set(keep)
    titles = {slug: graph.title_of(graph.split_frontmatter(text)[1], slug)
              for slug, text in keep.items()}

    pattern = _company_pattern(company_names(conn))
    for slug, text in keep.items():
        page = render(study_dir, slug, text, export_set, titles)
        bad = refusals(page)
        if bad:
            result.refused.append((slug, bad))
            continue
        hits = company_hits(page, pattern)
        if hits:
            result.warned.append((slug, hits))
        result.pages[slug] = page
        result.exported.append(slug)

    if dry_run:
        return result

    out_dir.mkdir(parents=True, exist_ok=True)
    result.deleted = _prune(out_dir, _read_manifest(out_dir),
                            set(result.exported))

    for slug, page in result.pages.items():
        (out_dir / f"{slug}.md").write_text(page, encoding="utf-8")
        svg = diagram_path(study_dir, slug)
        if svg.is_file():
            (out_dir / "diagrams").mkdir(exist_ok=True)
            shutil.copyfile(svg, out_dir / "diagrams" / f"{slug}.svg")

    manifest = {slug: {"reviewed_at": rows[slug].get("reviewed_at"),
                       "content_hash": rows[slug].get("content_hash")}
                for slug in result.exported}
    (out_dir / MANIFEST).write_text(
        yaml.safe_dump(manifest, sort_keys=True, allow_unicode=True),
        encoding="utf-8")
    return result


def audit(study_dir: Path, conn=None) -> Result:
    """Render every technical topic as if it were approved, and report.

    A dry run that ignores the publish flags entirely, so you can ask "is
    there anything in this folder I could not publish?" before flagging a
    single topic. Writes nothing and approves nothing.
    """
    study_dir = Path(study_dir)
    result = Result()
    keep = {}
    for f in graph.topic_files(study_dir):
        text = f.read_text(encoding="utf-8")
        meta, _ = graph.split_frontmatter(text)
        if graph.track_of(f.stem, meta) == "resume":
            result.skipped.append((f.stem, "resume drill; never published"))
            continue
        keep[f.stem] = text

    export_set = set(keep)
    titles = {slug: graph.title_of(graph.split_frontmatter(text)[1], slug)
              for slug, text in keep.items()}
    pattern = _company_pattern(company_names(conn))
    for slug, text in keep.items():
        page = render(study_dir, slug, text, export_set, titles)
        bad = refusals(page)
        if bad:
            result.refused.append((slug, bad))
            continue
        hits = company_hits(page, pattern)
        if hits:
            result.warned.append((slug, hits))
        result.pages[slug] = page
        result.exported.append(slug)
    return result


# ── CLI ─────────────────────────────────────────────────────────────

def _report(result: Result, dry_run: bool):
    verb = "would publish" if dry_run else "published"
    print(f"{verb} {len(result.exported)} topic(s), "
          f"skipped {len(result.skipped)}, refused {len(result.refused)}")
    for slug in result.exported:
        print(f"  + {slug}")
    for slug, why in result.skipped:
        print(f"  - {slug}: {why}")
    for slug, names in result.warned:
        print(f"  ! {slug}: mentions {', '.join(names)} - re-read before "
              f"this goes live")
    for slug, why in result.refused:
        print(f"  x {slug}: {'; '.join(why)}")
    for path in result.deleted:
        print(f"  d removed {path}")
    if result.refused:
        print("Nothing was written for the refused topic(s).")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "check"
    if cmd not in ("check", "export") or cmd == "export" and len(argv) != 2:
        print("usage: python -m jobscout.publish check\n"
              "       python -m jobscout.publish export <archive>/content/study",
              file=sys.stderr)
        return 1

    study = settings.study_dir()
    conn = None
    if Path(db.DB_PATH).exists():                   # never create one here
        try:
            conn = db.connect()
        except sqlite3.Error:
            pass
    try:
        if cmd == "check":
            result = export(study, Path("."), conn, dry_run=True)
        else:
            result = export(study, Path(argv[1]), conn)
    finally:
        if conn is not None:
            conn.close()
    _report(result, cmd == "check")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
