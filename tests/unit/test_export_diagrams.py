"""Diagram export: the Node exporter and the Python wrapper around it.

Two halves, deliberately separate. The first half runs the real toolchain over
a hand-written fixture scene and only runs where Node and `tools/node_modules`
are actually present, so a clean checkout and CI both skip it instead of
failing. The second half is pure Python: it proves the wrapper degrades
politely on a machine that never installed Node, which is the case the app
actually has to survive.

Every fixture here is synthetic. `tests/unit/fixtures/diagrams/demo.excalidraw`
is hand-written: two labelled rectangles with bound text and two bound arrows,
one of them labelled, which is exactly the shape the export has to survive
(bound text and arrow bindings are the parts a naive exporter drops).
"""
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from jobscout import settings

ROOT = Path(__file__).resolve().parent.parent.parent
TOOLS = ROOT / "tools"
FIXTURE = Path(__file__).parent / "fixtures" / "diagrams" / "demo.excalidraw"


def load_wrapper():
    """`tools/export_diagrams.py` is a script, not a package module, so it is
    imported by path rather than by name."""
    spec = importlib.util.spec_from_file_location(
        "export_diagrams_under_test", TOOLS / "export_diagrams.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def wrapper():
    return load_wrapper()


def write_scene(diagrams: Path, slug: str) -> Path:
    """The fixture scene, copied under a slug name in a throwaway folder."""
    diagrams.mkdir(parents=True, exist_ok=True)
    dest = diagrams / f"{slug}.excalidraw"
    dest.write_text(FIXTURE.read_text())
    return dest


# ── C3: the real exporter over the real fixture ─────────────────────

toolchain = pytest.mark.skipif(
    shutil.which("node") is None or not (TOOLS / "node_modules").is_dir(),
    reason="diagram export needs node and tools/node_modules (cd tools && npm install)")


@toolchain
def test_exports_the_fixture_scene_to_svg(tmp_path, wrapper):
    """The fixture's bound text and bound arrows survive the export: both
    container labels and the arrow label come out as text, and the drawing
    itself comes out as paths."""
    study = tmp_path / "study"
    write_scene(study / "diagrams", "demo-topic")

    assert wrapper.run(study, []) == 0

    svg = (study / "diagrams" / "demo-topic.svg").read_text()
    assert "Gatewaylabelone" in svg              # bound text, rectangle one
    assert "Backendlabeltwo" in svg              # bound text, rectangle two
    assert "Arrowlabelthree" in svg              # bound text on an arrow
    assert svg.count("<path") >= 2               # the shapes and the arrows
    assert "#ffffff" in svg                      # white background, not none


@toolchain
def test_export_skips_a_current_svg_and_redoes_a_stale_one(tmp_path, wrapper, capsys):
    """A diagram is re-exported when the scene moved under it, and left alone
    when it did not - mtime is the same staleness signal the rest of the study
    folder uses."""
    study = tmp_path / "study"
    scene = write_scene(study / "diagrams", "demo-topic")
    svg = study / "diagrams" / "demo-topic.svg"

    wrapper.run(study, [])
    first = svg.stat().st_mtime

    wrapper.run(study, [])
    assert "1 already current" in capsys.readouterr().out
    assert svg.stat().st_mtime == first

    scene.touch()                                # the scene moved
    wrapper.run(study, [])
    assert "1 exported" in capsys.readouterr().out
    assert svg.stat().st_mtime > first


# ── the wrapper without a toolchain, which is most machines ─────────

def test_wrapper_skips_cleanly_when_node_is_missing(tmp_path, wrapper, monkeypatch, capsys):
    """No Node means no picture, not a broken run: the wrapper says what is
    missing, points at the manual fallback, exits 0 and writes nothing."""
    study = tmp_path / "study"
    write_scene(study / "diagrams", "demo-topic")
    monkeypatch.setattr(wrapper.shutil, "which", lambda name: None)

    assert wrapper.run(study, []) == 0

    out = capsys.readouterr().out
    assert "node is not on PATH" in out
    assert "excalidraw.com" in out               # the manual fallback
    assert not (study / "diagrams" / "demo-topic.svg").exists()


def test_wrapper_skips_cleanly_without_node_modules(tmp_path, wrapper, monkeypatch, capsys):
    """Node present but the packages never installed is the other half of the
    same case, and gets the same treatment."""
    study = tmp_path / "study"
    write_scene(study / "diagrams", "demo-topic")
    monkeypatch.setattr(wrapper.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(wrapper, "NODE_MODULES", tmp_path / "never-installed")

    assert wrapper.run(study, []) == 0
    assert "never-installed" in capsys.readouterr().out


def test_no_diagrams_folder_is_not_an_error(tmp_path, wrapper, capsys):
    """Most study folders have no diagrams yet; that is a state, not a fault."""
    assert wrapper.run(tmp_path / "study", []) == 0
    assert "nothing to export" in capsys.readouterr().out


def test_staleness_is_missing_or_older(tmp_path, wrapper):
    scene = tmp_path / "a.excalidraw"
    scene.write_text("{}")
    svg = tmp_path / "a.svg"
    assert wrapper.is_stale(scene, svg)          # no svg at all
    svg.write_text("<svg/>")
    import os
    os.utime(svg, (scene.stat().st_mtime + 10,) * 2)
    assert not wrapper.is_stale(scene, svg)
    os.utime(svg, (scene.stat().st_mtime - 10,) * 2)
    assert wrapper.is_stale(scene, svg)


def test_slugs_limit_the_export_set(tmp_path, wrapper):
    diagrams = tmp_path / "diagrams"
    write_scene(diagrams, "one")
    write_scene(diagrams, "two")
    assert [p.stem for p in wrapper.sources(diagrams, [])] == ["one", "two"]
    assert [p.stem for p in wrapper.sources(diagrams, ["two"])] == ["two"]


# ── C5: the real folder, reported and never asserted on ─────────────

def test_real_diagrams_report(capsys):
    """Runs only where the private study folder exists (never in CI). Prints
    the technical topics that have no SVG yet and asserts nothing about them -
    a topic without a diagram is a topic waiting for one, not a failure."""
    from jobscout import graph

    sdir = settings.study_dir()
    if not (sdir / "topics").is_dir():
        pytest.skip("no study folder in this checkout")

    missing, drawn = [], 0
    for f in graph.topic_files(sdir):
        slug = f.stem
        try:
            meta, _ = graph.split_frontmatter(f.read_text())
        except OSError:
            continue
        if graph.track_of(slug, meta) == "resume":
            continue                             # drills are drilled, not drawn
        if (sdir / "diagrams" / f"{slug}.svg").exists():
            drawn += 1
        else:
            missing.append(slug)

    with capsys.disabled():
        print(f"\nreal study folder: {drawn} technical topic(s) illustrated, "
              f"{len(missing)} without an svg")
        for slug in missing:
            print(f"  {slug}: no diagrams/{slug}.svg")
    assert isinstance(missing, list)
