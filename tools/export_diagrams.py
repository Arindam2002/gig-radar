"""Export study diagrams: `<study>/diagrams/<slug>.excalidraw` -> `<slug>.svg`.

    python tools/export_diagrams.py                # every stale diagram
    python tools/export_diagrams.py --study DIR    # a different study folder
    python tools/export_diagrams.py rag-basics     # just these slugs

The Excalidraw file is the source of truth and the SVG is what every surface
embeds (the dashboard topic page, the tutor, the archive export). A diagram is
re-exported when its SVG is missing or older than its source; use --force to
redo the lot.

The actual rendering happens in Node (`tools/export_diagram.mjs`), because the
only thing that knows how to draw an Excalidraw scene is Excalidraw. Node is
optional: the app, the dashboard and the tests all work without it, and this
script says so and exits 0 rather than failing a machine that never installed
it. See tools/README.md for the setup and for the manual fallback.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
EXPORTER = TOOLS / "export_diagram.mjs"
NODE_MODULES = TOOLS / "node_modules"

SETUP_HINT = (
    "Diagram export needs Node and the packages in tools/.\n"
    "  cd tools && npm install\n"
    "Without it, export by hand: open the .excalidraw file at excalidraw.com, "
    "then Export image > SVG and save it next to the source."
)


def _default_study() -> Path:
    """The study folder jobscout uses, imported lazily so this script still
    runs (and still prints its skip message) from a bare checkout."""
    sys.path.insert(0, str(ROOT))
    from jobscout import settings

    return settings.study_dir()


def toolchain_missing() -> str | None:
    """Why export cannot run, or None when it can."""
    if shutil.which("node") is None:
        return "node is not on PATH."
    if not NODE_MODULES.is_dir():
        return f"{NODE_MODULES} is missing."
    if not EXPORTER.exists():
        return f"{EXPORTER} is missing."
    return None


def sources(diagrams: Path, slugs: list[str]) -> list[Path]:
    """The `.excalidraw` files to consider, in a stable order."""
    if slugs:
        return [diagrams / f"{slug}.excalidraw" for slug in slugs]
    return sorted(diagrams.glob("*.excalidraw"))


def is_stale(src: Path, svg: Path) -> bool:
    """True when the SVG needs redoing: it is missing, or the scene moved
    under it. mtime is the same signal the study folder uses everywhere else."""
    if not svg.exists():
        return True
    return src.stat().st_mtime > svg.stat().st_mtime


def export_one(src: Path, svg: Path) -> tuple[bool, str]:
    """Render one scene. Returns (ok, message); never raises on a bad file."""
    try:
        proc = subprocess.run(
            ["node", str(EXPORTER), str(src), str(svg)],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not run node: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, detail[0] if detail else f"node exited {proc.returncode}"
    return True, str(svg)


def run(study: Path, slugs: list[str], force: bool = False) -> int:
    diagrams = Path(study) / "diagrams"
    if not diagrams.is_dir():
        print(f"No diagrams folder at {diagrams}; nothing to export.")
        return 0

    why = toolchain_missing()
    if why:
        print(f"Skipping diagram export: {why}")
        print(SETUP_HINT)
        return 0

    todo = sources(diagrams, slugs)
    if not todo:
        print(f"No .excalidraw files in {diagrams}.")
        return 0

    exported, skipped, failed = 0, 0, 0
    for src in todo:
        if not src.exists():
            print(f"  {src.name}: no such scene file")
            failed += 1
            continue
        svg = src.with_suffix(".svg")
        if not force and not is_stale(src, svg):
            skipped += 1
            continue
        ok, message = export_one(src, svg)
        if ok:
            print(f"  {src.stem}: {svg.name}")
            exported += 1
        else:
            print(f"  {src.stem}: export failed - {message}")
            failed += 1

    print(f"{exported} exported, {skipped} already current, {failed} failed.")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("slugs", nargs="*", help="topic slugs; default is all of them")
    ap.add_argument("--study", type=Path, default=None,
                    help="study folder (default: the one jobscout uses)")
    ap.add_argument("--force", action="store_true",
                    help="re-export even when the SVG is newer than the scene")
    args = ap.parse_args(argv)
    study = args.study or _default_study()
    return run(study, args.slugs, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
