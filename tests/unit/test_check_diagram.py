"""Geometry lint for study diagrams.

Every scene here is synthetic and built in-process: two boxes and an arrow,
nudged into whichever collision the rule under test is about. Nothing reads the
real study folder, so the numbers in these tests are the contract rather than a
snapshot of whatever the diagrams happen to look like today.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import check_diagram as cd  # noqa: E402


# --------------------------------------------------------------- builders
def rect(rid, x, y, w=200, h=80, *, opacity=100, bg="#a5d8ff", bound=None):
    return {
        "id": rid, "type": "rectangle", "x": x, "y": y, "width": w, "height": h,
        "angle": 0, "strokeColor": "#1e1e1e", "backgroundColor": bg,
        "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid",
        "roughness": 1, "opacity": opacity, "groupIds": [], "frameId": None,
        "roundness": {"type": 3}, "seed": 1, "version": 1, "versionNonce": 1,
        "isDeleted": False, "boundElements": list(bound or []), "updated": 1,
        "link": None, "locked": False,
    }


def text(tid, x, y, body="label", *, size=20, container=None, w=None, h=None):
    lines = body.split("\n")
    return {
        "id": tid, "type": "text", "x": x, "y": y,
        "width": w if w is not None else max(len(ln) for ln in lines) * size * 0.55,
        "height": h if h is not None else len(lines) * size * 1.25,
        "angle": 0, "strokeColor": "#1e1e1e", "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid",
        "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": None, "seed": 1, "version": 1, "versionNonce": 1,
        "isDeleted": False, "boundElements": [], "updated": 1, "link": None,
        "locked": False, "text": body, "originalText": body, "fontSize": size,
        "fontFamily": 5, "textAlign": "center", "verticalAlign": "middle",
        "containerId": container, "lineHeight": 1.25,
    }


def arrow(aid, pts, *, start=None, end=None, style="solid", bound=None,
          head="arrow"):
    x0, y0 = pts[0]
    return {
        "id": aid, "type": "arrow", "x": x0, "y": y0,
        "width": max(p[0] for p in pts) - min(p[0] for p in pts),
        "height": max(p[1] for p in pts) - min(p[1] for p in pts),
        "angle": 0, "strokeColor": "#1e1e1e", "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": style,
        "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": {"type": 2}, "seed": 1, "version": 1, "versionNonce": 1,
        "isDeleted": False, "boundElements": list(bound or []), "updated": 1,
        "link": None, "locked": False,
        "points": [[p[0] - x0, p[1] - y0] for p in pts],
        "startBinding": {"elementId": start, "focus": 0, "gap": 4} if start else None,
        "endBinding": {"elementId": end, "focus": 0, "gap": 4} if end else None,
        "startArrowhead": None, "endArrowhead": head,
    }


def label(shape, tid, body="label", size=20):
    """A container label, wired both ways like a real scene."""
    t = text(tid, shape["x"] + 10, shape["y"] + 20, body, size=size,
             container=shape["id"])
    shape["boundElements"].append({"id": tid, "type": "text"})
    return t


def scene(*elements):
    return {"type": "excalidraw", "version": 2,
            "source": "https://excalidraw.com", "elements": list(elements),
            "appState": {"viewBackgroundColor": "#ffffff"}, "files": {}}


def clean_scene():
    """Two labelled boxes and a bound arrow between them, plus a free-standing
    arrow label off to the side. Nothing overlaps anything."""
    a = rect("a", 0, 0)
    b = rect("b", 500, 0)
    ar = arrow("ar", [(200, 40), (500, 40)], start="a", end="b")
    a["boundElements"].append({"id": "ar", "type": "arrow"})
    b["boundElements"].append({"id": "ar", "type": "arrow"})
    return scene(a, label(a, "a_lbl"), b, label(b, "b_lbl"), ar,
                 text("ar_lbl", 300, 60, "sends", size=16))


def rules(findings, rule=None, severity=None):
    return [f for f in findings
            if (rule is None or f["rule"] == rule)
            and (severity is None or f["severity"] == severity)]


def ids(findings):
    return {i for f in findings for i in f["elements"]}


# --------------------------------------------------------------- clean
def test_clean_scene_has_no_findings():
    assert cd.lint_scene(clean_scene()) == []


def test_container_label_is_not_reported_against_its_own_container():
    """The whole point of a label is to sit on its box."""
    a = rect("a", 0, 0)
    doc = scene(a, label(a, "a_lbl"))
    assert rules(cd.lint_scene(doc), "text-over-shape") == []


# --------------------------------------------------------------- rule a
def test_a_text_over_text_is_an_error():
    doc = scene(text("t1", 0, 0, "first", size=20, w=200, h=25),
                text("t2", 20, 5, "second", size=20, w=200, h=25))
    found = rules(cd.lint_scene(doc), "text-over-text", cd.ERROR)
    assert len(found) == 1
    assert ids(found) == {"t1", "t2"}


def test_a_text_boxes_touching_within_tolerance_pass():
    doc = scene(text("t1", 0, 0, "first", w=200, h=25),
                text("t2", 199, 0, "second", w=200, h=25))
    assert rules(cd.lint_scene(doc), "text-over-text") == []


# --------------------------------------------------------------- rule b
def test_b_text_over_a_shape_it_does_not_label_is_an_error():
    box = rect("box", 0, 0)
    doc = scene(box, label(box, "box_lbl"), text("stray", 50, 30, "stray"))
    found = rules(cd.lint_scene(doc), "text-over-shape", cd.ERROR)
    assert len(found) == 1
    assert ids(found) == {"stray", "box"}


@pytest.mark.parametrize("kwargs", [{"opacity": 30}, {"bg": "transparent"}])
def test_b_zones_are_exempt(kwargs):
    """A translucent band and a dashed frame both exist to be drawn on."""
    zone = rect("zone", 0, 0, 600, 400, **kwargs)
    doc = scene(zone, text("stray", 50, 30, "stray"))
    assert rules(cd.lint_scene(doc), "text-over-shape") == []


# --------------------------------------------------------------- rule c
def test_c_arrow_through_an_unrelated_box_is_an_error():
    a, b = rect("a", 0, 0), rect("b", 900, 0)
    mid = rect("mid", 400, 0)
    ar = arrow("ar", [(200, 40), (900, 40)], start="a", end="b")
    a["boundElements"].append({"id": "ar", "type": "arrow"})
    b["boundElements"].append({"id": "ar", "type": "arrow"})
    found = rules(cd.lint_scene(scene(a, b, mid, ar)), "arrow-through-shape",
                  cd.ERROR)
    assert len(found) == 1
    assert ids(found) == {"ar", "mid"}


def test_c_is_checked_per_segment_and_includes_dashed():
    """The first leg of the elbow is clear; the second one runs through mid."""
    a, b = rect("a", 0, 0), rect("b", 900, 400)
    mid = rect("mid", 400, 360)
    ar = arrow("ar", [(200, 40), (200, 400), (900, 400)], start="a", end="b",
               style="dashed")
    a["boundElements"].append({"id": "ar", "type": "arrow"})
    b["boundElements"].append({"id": "ar", "type": "arrow"})
    found = rules(cd.lint_scene(scene(a, b, mid, ar)), "arrow-through-shape",
                  cd.ERROR)
    assert len(found) == 1
    assert ids(found) == {"ar", "mid"}
    assert "segment 2" in found[0]["message"]


def test_c_an_arrow_may_cross_the_boxes_it_is_bound_to():
    a, b = rect("a", 0, 0), rect("b", 500, 0)
    ar = arrow("ar", [(100, 40), (600, 40)], start="a", end="b")
    a["boundElements"].append({"id": "ar", "type": "arrow"})
    b["boundElements"].append({"id": "ar", "type": "arrow"})
    assert rules(cd.lint_scene(scene(a, b, ar)), "arrow-through-shape") == []


def test_c_an_unbound_pointer_that_ends_on_a_box_is_not_a_collision():
    box = rect("box", 400, 0)
    ar = arrow("ar", [(200, 40), (450, 40)])
    assert rules(cd.lint_scene(scene(box, ar)), "arrow-through-shape") == []


# --------------------------------------------------------------- rule d
def test_d_arrow_across_someone_elses_label_is_an_error():
    a, b = rect("a", 0, 0), rect("b", 900, 0)
    ar = arrow("ar", [(200, 40), (900, 40)], start="a", end="b")
    a["boundElements"].append({"id": "ar", "type": "arrow"})
    b["boundElements"].append({"id": "ar", "type": "arrow"})
    other = text("other", 450, 25, "in the way", size=20, w=200, h=30)
    found = rules(cd.lint_scene(scene(a, b, ar, other)), "arrow-over-text",
                  cd.ERROR)
    assert len(found) == 1
    assert ids(found) == {"ar", "other"}


def test_d_an_arrow_does_not_cross_its_own_label():
    ar = arrow("ar", [(0, 40), (400, 40)],
               bound=[{"id": "ar_lbl", "type": "text"}])
    lbl = text("ar_lbl", 150, 25, "sends", size=16, container="ar", w=100, h=30)
    assert rules(cd.lint_scene(scene(ar, lbl)), "arrow-over-text") == []


# --------------------------------------------------------------- rule e
def test_e_label_bound_to_an_arrow_is_an_error_that_states_the_fix():
    ar = arrow("ar", [(0, 40), (400, 40)],
               bound=[{"id": "ar_lbl", "type": "text"}])
    lbl = text("ar_lbl", 150, 25, "sends", size=16, container="ar", w=100, h=30)
    found = rules(cd.lint_scene(scene(ar, lbl)), "label-bound-to-arrow",
                  cd.ERROR)
    assert len(found) == 1
    assert ids(found) == {"ar_lbl", "ar"}
    assert cd.BOUND_LABEL_FIX in found[0]["message"]


def test_e_a_free_standing_arrow_label_is_fine():
    """Same label, same place, just not bound. This is the house style."""
    ar = arrow("ar", [(0, 40), (400, 40)])
    lbl = text("ar_lbl", 150, 60, "sends", size=16, w=100, h=25)
    assert rules(cd.lint_scene(scene(ar, lbl)), "label-bound-to-arrow") == []


def test_e_a_label_bound_to_a_shape_is_fine():
    box = rect("box", 0, 0)
    assert rules(cd.lint_scene(scene(box, label(box, "box_lbl"))),
                 "label-bound-to-arrow") == []


# --------------------------------------------------------------- rule f
def test_f_arrowheads_landing_together_warn():
    target = rect("t", 400, 0)
    a1 = arrow("a1", [(0, 0), (400, 40)], end="t")
    a2 = arrow("a2", [(0, 200), (400, 45)], end="t")
    target["boundElements"] += [{"id": "a1", "type": "arrow"},
                                {"id": "a2", "type": "arrow"}]
    found = rules(cd.lint_scene(scene(target, a1, a2)), "arrowhead-pileup",
                  cd.WARN)
    assert len(found) == 1
    assert ids(found) == {"a1", "a2", "t"}


def test_f_arrowheads_spread_along_a_side_do_not_warn():
    target = rect("t", 400, 0)
    a1 = arrow("a1", [(0, 0), (400, 15)], end="t")
    a2 = arrow("a2", [(0, 200), (400, 65)], end="t")
    target["boundElements"] += [{"id": "a1", "type": "arrow"},
                                {"id": "a2", "type": "arrow"}]
    assert rules(cd.lint_scene(scene(target, a1, a2)), "arrowhead-pileup") == []


# --------------------------------------------------------------- rule g
def test_g_a_label_wider_than_its_box_warns_about_overflow():
    box = rect("box", 0, 0, 120, 60)
    doc = scene(box, label(box, "box_lbl", "a very long label indeed"))
    found = rules(cd.lint_scene(doc), "text-overflow", cd.WARN)
    assert len(found) == 1
    assert ids(found) == {"box_lbl", "box"}
    assert "overflow" in found[0]["message"]


def test_g_a_label_that_fits_does_not_warn():
    box = rect("box", 0, 0, 300, 80)
    doc = scene(box, label(box, "box_lbl", "short"))
    assert rules(cd.lint_scene(doc), "text-overflow") == []


# --------------------------------------------------------------- structural
def test_binding_that_is_not_listed_back_is_an_error():
    box = rect("box", 0, 0)                       # no boundElements entry
    doc = scene(box, text("t", 10, 20, "label", container="box"))
    assert len(rules(cd.lint_scene(doc), "binding", cd.ERROR)) == 1


def test_binding_to_a_missing_element_is_an_error():
    doc = scene(text("t", 10, 20, "label", container="ghost"))
    found = rules(cd.lint_scene(doc), "binding", cd.ERROR)
    assert len(found) == 1
    assert "ghost" in found[0]["message"]


def test_a_stranded_element_is_out_of_bounds():
    doc = scene(rect("a", 0, 0), rect("b", 300, 0), rect("far", 99000, 0))
    found = rules(cd.lint_scene(doc), "out-of-bounds", cd.ERROR)
    assert ids(found) == {"far"}


def test_font_under_16_is_an_error_but_a_short_caption_at_14_is_not():
    doc = scene(text("tiny", 0, 0, "unreadable body text here", size=12),
                text("cap", 0, 200, "(durable store)", size=14),
                text("note", 0, 400, "four gates", size=14))
    found = rules(cd.lint_scene(doc), "font-too-small", cd.ERROR)
    assert ids(found) == {"tiny"}


def test_a_small_labelled_box_warns_rather_than_failing():
    box = rect("box", 0, 0, 80, 40)
    found = rules(cd.lint_scene(scene(box, label(box, "box_lbl", "CB"))),
                  "box-too-small")
    assert len(found) == 1
    assert found[0]["severity"] == cd.WARN


# --------------------------------------------------------------- cli
def write(tmp_path, name, doc):
    d = tmp_path / "diagrams"
    d.mkdir(exist_ok=True)
    p = d / f"{name}.excalidraw"
    p.write_text(json.dumps(doc))
    return p


def test_exit_code_is_zero_for_a_clean_scene(tmp_path, capsys):
    write(tmp_path, "ok", clean_scene())
    assert cd.main(["--study", str(tmp_path), "ok"]) == 0
    assert "0 error(s)" in capsys.readouterr().out


def test_exit_code_is_zero_when_there_are_only_warnings(tmp_path, capsys):
    box = rect("box", 0, 0, 120, 60)
    write(tmp_path, "warned", scene(box, label(box, "box_lbl",
                                               "a very long label indeed")))
    assert cd.main(["--study", str(tmp_path), "warned"]) == 0
    assert "1 warning(s)" in capsys.readouterr().out


def test_exit_code_is_one_on_any_error(tmp_path):
    ar = arrow("ar", [(0, 40), (400, 40)],
               bound=[{"id": "lbl", "type": "text"}])
    write(tmp_path, "bad", scene(
        ar, text("lbl", 150, 25, "sends", size=16, container="ar")))
    assert cd.main(["--study", str(tmp_path), "bad"]) == 1


def test_a_missing_slug_reports_and_fails(tmp_path, capsys):
    assert cd.main(["--study", str(tmp_path), "nope"]) == 1
    assert "no scene file" in capsys.readouterr().out


def test_the_report_is_written_next_to_the_scene(tmp_path):
    src = write(tmp_path, "ok", clean_scene())
    cd.main(["--study", str(tmp_path), "ok"])
    report = json.loads((src.parent / "ok.lint.json").read_text())
    assert report["errors"] == 0 and report["findings"] == []
    assert report["elements"] == 6


def test_a_path_target_works_without_a_study_folder(tmp_path):
    src = write(tmp_path, "ok", clean_scene())
    assert cd.main([str(src)]) == 0


# --------------------------------------------------------------- png
def test_png_is_skipped_cleanly_when_the_svg_is_missing(tmp_path, capsys):
    write(tmp_path, "ok", clean_scene())
    assert cd.main(["--study", str(tmp_path), "--png", "ok"]) == 0
    out = capsys.readouterr().out
    assert "png skipped" in out and "no SVG" in out


SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" '
    'viewBox="0 0 200 100"><rect x="10" y="10" width="180" height="80" '
    'fill="#a5d8ff" stroke="#1e1e1e"/></svg>'
)


def test_png_is_rendered_at_2x_when_chromium_is_available(tmp_path):
    pytest.importorskip("playwright", reason="playwright is not installed")
    src = write(tmp_path, "ok", clean_scene())
    src.with_suffix(".svg").write_text(SVG)
    png = src.with_suffix(".png")
    ok, message = cd.render_png(src.with_suffix(".svg"), png)
    if not ok:
        pytest.skip(f"chromium did not launch: {message}")
    assert png.exists() and png.stat().st_size > 0
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    # 2x device scale factor on a 200x100 SVG
    width = int.from_bytes(png.read_bytes()[16:20], "big")
    assert width == 400


def test_the_render_holder_file_is_cleaned_up(tmp_path):
    src = write(tmp_path, "ok", clean_scene())
    svg = src.with_suffix(".svg")
    svg.write_text(SVG)
    cd.render_png(svg, src.with_suffix(".png"))
    assert not list(src.parent.glob("*__render__*"))
