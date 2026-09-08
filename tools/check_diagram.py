"""Check a study diagram for being correct and readable.

    python tools/check_diagram.py <slug>              # one diagram
    python tools/check_diagram.py --study DIR <slug>  # a different study folder
    python tools/check_diagram.py --png <slug>        # also rasterise the SVG
    python tools/check_diagram.py path/to/x.excalidraw

The `.excalidraw` scene is the source of truth and the SVG is what every
surface embeds, so a diagram that exports cleanly can still be wrong: a label
sitting on top of another label, an arrow cutting through a box it has nothing
to do with, four arrowheads landing on the same corner. None of that shows up
in a JSON validity check. This script measures the geometry instead.

Two kinds of finding come out:

  error - the picture is wrong or unreadable. Exit code 1.
  warn  - worth a look, probably fine. Exit code 0.

A JSON copy of the report lands next to the scene as `<slug>.lint.json` so a
later run (or a human) can diff it.

The rules are documented in tools/README.md, under "Checking a diagram".
This module is stdlib-only; Playwright is imported lazily and only for --png.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent

# --------------------------------------------------------------- tunables
MIN_FONT = 16          # house style: nothing smaller reads in the dashboard
SMALL_FONT_OK = 14     # allowed for parentheticals and short captions
CAPTION_CHARS = 30     # "short caption" means fewer than this many characters
MIN_BOX_W, MIN_BOX_H = 120, 60   # a labelled rectangle needs room to breathe
TEXT_OVERLAP = 2.0     # two text boxes may share this many px per axis
SHAPE_INSET = 4.0      # arrows may clip this far into a box before it counts
TEXT_INSET = 2.0       # ... and this far into a text box
TOUCH_PAD = 8.0        # an unbound arrow may end this far inside a shape
ZONE_EDGE_TOL = 3.0    # a text box may cross a zone's outline by this much
PILEUP_GAP = 12.0      # two arrowheads closer than this on one side pile up
STRAY_PAD = 240.0      # an element this far outside everything else is lost
CHAR_W = 0.55          # Excalifont glyph width as a fraction of the font size
LABEL_PAD = 10.0       # Excalidraw's own padding around a bound label
COORD_LIMIT = 1e6      # beyond this a coordinate is a bug, not a layout

CONNECTORS = ("arrow", "line")
SHAPES = ("rectangle", "ellipse", "diamond", "image")

ERROR, WARN = "error", "warn"

BOUND_LABEL_FIX = "place the label beside the arrow as free text"


# --------------------------------------------------------------- geometry
def _finite(*vals) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals)


def points_of(el: dict) -> list[tuple[float, float]]:
    """A connector's waypoints in absolute scene coordinates."""
    pts = el.get("points") or []
    x, y = el.get("x", 0.0), el.get("y", 0.0)
    out = []
    for p in pts:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        if not _finite(p[0], p[1]):
            continue
        out.append((float(x) + float(p[0]), float(y) + float(p[1])))
    return out


def box_of(el: dict) -> tuple[float, float, float, float] | None:
    """(x0, y0, x1, y1) for any element, or None when the geometry is junk.

    Connectors are measured from their points, because an arrow's own x/y is
    its first point and that is not always the corner of its bounding box.
    """
    if el.get("type") in CONNECTORS and el.get("points"):
        pts = points_of(el)
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)
    x, y = el.get("x"), el.get("y")
    w, h = el.get("width", 0.0), el.get("height", 0.0)
    if not _finite(x, y, w, h):
        return None
    return float(x), float(y), float(x) + float(w), float(y) + float(h)


def overlap(a, b) -> tuple[float, float]:
    """How far two boxes intersect, per axis. Negative means they do not."""
    return (min(a[2], b[2]) - max(a[0], b[0]),
            min(a[3], b[3]) - max(a[1], b[1]))


def inset(box, by: float):
    """Shrink a box by `by` on every side, never past its own centre."""
    x0, y0, x1, y1 = box
    dx = min(by, max(0.0, (x1 - x0) / 2 - 0.01))
    dy = min(by, max(0.0, (y1 - y0) / 2 - 0.01))
    return x0 + dx, y0 + dy, x1 - dx, y1 - dy


def inflate(box, by: float):
    return box[0] - by, box[1] - by, box[2] + by, box[3] + by


def union(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def contains(box, pt, pad: float = 0.0) -> bool:
    return (box[0] - pad <= pt[0] <= box[2] + pad
            and box[1] - pad <= pt[1] <= box[3] + pad)


def corners(box):
    return ((box[0], box[1]), (box[2], box[1]),
            (box[2], box[3]), (box[0], box[3]))


def rect_inside(inner, outer, tol: float = 0.0) -> bool:
    """Is `inner` wholly within `outer`, forgiving `tol` px of overhang?"""
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def ellipse_of(box, grow: float = 0.0):
    """(cx, cy, rx, ry) of the ellipse drawn in `box`, radii nudged by `grow`."""
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2,
            max((box[2] - box[0]) / 2 + grow, 0.01),
            max((box[3] - box[1]) / 2 + grow, 0.01))


def pt_in_ellipse(pt, e) -> bool:
    cx, cy, rx, ry = e
    return ((pt[0] - cx) / rx) ** 2 + ((pt[1] - cy) / ry) ** 2 <= 1.0


def box_hits_ellipse(box, e) -> bool:
    """Does an axis-aligned box touch the ellipse's filled area?

    Dividing by rx and ry turns the ellipse into the unit circle and leaves the
    box axis-aligned, so this reduces to the distance from the origin to a
    rectangle, which is exact rather than a corner-sampling approximation.
    """
    cx, cy, rx, ry = e
    x0, x1 = (box[0] - cx) / rx, (box[2] - cx) / rx
    y0, y1 = (box[1] - cy) / ry, (box[3] - cy) / ry
    return math.hypot(max(x0, -x1, 0.0), max(y0, -y1, 0.0)) <= 1.0


def seg_box_hit(p0, p1, box, min_len: float = 1.0):
    """Where a segment crosses a box, or None.

    Liang-Barsky clipping. Returns the midpoint of the clipped portion so the
    report can say where the collision is. A segment that only grazes a corner
    (a clipped length under `min_len`) does not count.
    """
    x0, y0 = p0
    x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - box[0]), (dx, box[2] - x0),
                 (-dy, y0 - box[1]), (dy, box[3] - y0)):
        if p == 0:
            if q < 0:
                return None          # parallel to this slab and outside it
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return None
            t0 = max(t0, t)
        else:
            if t < t0:
                return None
            t1 = min(t1, t)
    if t0 > t1:
        return None
    if math.hypot(dx * (t1 - t0), dy * (t1 - t0)) < min_len:
        return None
    tm = (t0 + t1) / 2
    return x0 + dx * tm, y0 + dy * tm


def arrow_midpoint(el: dict) -> tuple[float, float] | None:
    """The point half way along a connector, by arc length."""
    pts = points_of(el)
    if len(pts) < 2:
        return None
    segs = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    half, acc = sum(segs) / 2, 0.0
    for i, d in enumerate(segs):
        if acc + d >= half:
            t = (half - acc) / d if d else 0.0
            return (pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t,
                    pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t)
        acc += d
    return pts[-1]


def render_boxes(live: list[dict], by_id: dict) -> dict:
    """Where each element actually lands in the exported SVG.

    Same as `box_of` with one correction that matters: a label bound to an
    arrow is re-centred on the arrow's midpoint at export time, whatever x/y
    the scene file stores. Linting the stored position would pass a diagram
    whose picture is wrong, so measure the position the exporter will use.
    """
    boxes = {}
    for e in live:
        b = box_of(e)
        if (b is not None and e.get("type") == "text"
                and (by_id.get(e.get("containerId")) or {}).get("type") in CONNECTORS):
            mid = arrow_midpoint(by_id[e["containerId"]])
            if mid:
                w, h = b[2] - b[0], b[3] - b[1]
                b = (mid[0] - w / 2, mid[1] - h / 2, mid[0] + w / 2, mid[1] + h / 2)
        boxes[e.get("id")] = b
    return boxes


def nearest_side(pt, box) -> str:
    x, y = pt
    d = {"left": abs(x - box[0]), "right": abs(x - box[2]),
         "top": abs(y - box[1]), "bottom": abs(y - box[3])}
    return min(d, key=lambda k: d[k])


# --------------------------------------------------------------- findings
def finding(rule: str, severity: str, elements, message: str, at=None) -> dict:
    out = {"rule": rule, "severity": severity,
           "elements": list(elements), "message": message}
    if at is not None:
        out["at"] = [round(at[0], 1), round(at[1], 1)]
    return out


def _label_text(el: dict) -> str:
    return el.get("text") or el.get("originalText") or ""


def _snip(el: dict, n: int = 28) -> str:
    text = " ".join(_label_text(el).split())
    return text if len(text) <= n else text[: n - 1] + "..."


def _is_zone(el: dict) -> bool:
    """A zone: a background band or a grouping frame, not a solid box.

    Zones exist to have things drawn on top of them and arrows running across
    their edge, so the overlap rules skip them. Two markers, both of which the
    house style uses: an opacity under 100 (a translucent band), or no fill at
    all (a dashed frame drawn round a region). A solid box is opacity 100 with
    a real background colour.
    """
    if el.get("type") not in SHAPES:
        return False
    if float(el.get("opacity", 100)) < 100:
        return True
    return (el.get("backgroundColor") or "transparent").lower() in (
        "transparent", "none", "")


# --------------------------------------------------------------- the lint
def lint_scene(doc: dict) -> list[dict]:
    """Every finding for one parsed scene, structural first then geometry."""
    els = doc.get("elements") or []
    live = [e for e in els if not e.get("isDeleted")]
    by_id = {e.get("id"): e for e in live}

    labels_by_container: dict = {}
    for e in live:
        if e.get("type") == "text" and e.get("containerId"):
            labels_by_container.setdefault(e["containerId"], e)

    boxes = render_boxes(live, by_id)
    findings: list[dict] = []
    findings += _structural(live, by_id, boxes)
    findings += _fonts_and_sizes(live, labels_by_container, boxes)
    findings += _geometry(live, by_id, boxes)
    return findings


def _structural(live, by_id, boxes) -> list[dict]:
    out = []
    seen = set()
    for e in live:
        eid = e.get("id")
        if eid in seen:
            out.append(finding("duplicate-id", ERROR, [eid],
                               f"{eid}: two elements share this id"))
        seen.add(eid)

    for e in live:
        eid = e.get("id")
        # a label must point at a container that points back
        if e.get("type") == "text" and e.get("containerId"):
            cid = e["containerId"]
            c = by_id.get(cid)
            if c is None:
                out.append(finding("binding", ERROR, [eid],
                                   f"{eid}: containerId {cid} is not in the scene"))
            elif not any(r.get("id") == eid for r in (c.get("boundElements") or [])):
                out.append(finding("binding", ERROR, [eid, cid],
                                   f"{eid}: container {cid} does not list it back"))
        # an arrow binding must point at a shape that points back
        if e.get("type") in CONNECTORS:
            for key in ("startBinding", "endBinding"):
                b = e.get(key)
                if not b:
                    continue
                t = by_id.get(b.get("elementId"))
                if t is None:
                    out.append(finding(
                        "binding", ERROR, [eid],
                        f"{eid}: {key} -> {b.get('elementId')} is not in the scene"))
                elif not any(r.get("id") == eid for r in (t.get("boundElements") or [])):
                    out.append(finding(
                        "binding", ERROR, [eid, b["elementId"]],
                        f"{eid}: {key} target {b['elementId']} does not list it back"))
        # and everything listed in boundElements must exist and agree
        for ref in (e.get("boundElements") or []):
            t = by_id.get(ref.get("id"))
            if t is None:
                out.append(finding(
                    "binding", ERROR, [eid],
                    f"{eid}: boundElements -> {ref.get('id')} is not in the scene"))
                continue
            if ref.get("type") == "text" and t.get("containerId") != eid:
                out.append(finding(
                    "binding", ERROR, [eid, ref["id"]],
                    f"{eid}: bound text {ref['id']} belongs to another container"))
            if ref.get("type") == "arrow" and not any(
                    (t.get(k) or {}).get("elementId") == eid
                    for k in ("startBinding", "endBinding")):
                out.append(finding(
                    "binding", ERROR, [eid, ref["id"]],
                    f"{eid}: bound arrow {ref['id']} is not bound back"))

    # bounds: junk coordinates, and elements stranded away from the drawing
    drawable = {}
    for e in live:
        b = boxes.get(e.get("id"))
        if b is None or max(abs(v) for v in b) > COORD_LIMIT:
            out.append(finding("out-of-bounds", ERROR, [e.get("id")],
                               f"{e.get('id')}: coordinates are not drawable ({b})"))
            continue
        drawable[e["id"]] = b
    if len(drawable) > 1:
        for eid, b in drawable.items():
            others = union([v for k, v in drawable.items() if k != eid])
            ox, oy = overlap(b, inflate(others, STRAY_PAD))
            if ox < 0 or oy < 0:
                out.append(finding(
                    "out-of-bounds", ERROR, [eid],
                    f"{eid}: sits outside the scene, more than {STRAY_PAD:.0f}px "
                    f"clear of every other element",
                    at=(b[0], b[1])))
    return out


def _fonts_and_sizes(live, labels_by_container, boxes) -> list[dict]:
    out = []
    for e in live:
        eid = e.get("id")
        if e.get("type") == "text":
            size = float(e.get("fontSize", MIN_FONT))
            if size >= MIN_FONT:
                continue
            text = _label_text(e).strip()
            excused = (
                SMALL_FONT_OK <= size < MIN_FONT
                and (text.startswith("(")
                     or (not e.get("containerId") and len(text) < CAPTION_CHARS))
            )
            if not excused:
                out.append(finding(
                    "font-too-small", ERROR, [eid],
                    f"{eid}: font {size:g} is under {MIN_FONT} "
                    f"({SMALL_FONT_OK} is only allowed for a parenthetical or a "
                    f"standalone caption under {CAPTION_CHARS} characters)"))
        elif e.get("type") == "rectangle" and eid in labels_by_container:
            b = boxes.get(eid)
            if b is None:
                continue
            w, h = b[2] - b[0], b[3] - b[1]
            if w >= MIN_BOX_W and h >= MIN_BOX_H:
                continue
            out.append(finding(
                "box-too-small", WARN, [eid],
                f"{eid}: labelled rectangle is {w:.0f}x{h:.0f}, under the "
                f"{MIN_BOX_W}x{MIN_BOX_H} a label needs to breathe",
                at=(b[0], b[1])))
    return out


def _related_shapes(a: dict, shapes: list[dict], boxes: dict) -> set:
    """Shapes an arrow is legitimately touching.

    Bound ends count, and so does an endpoint that lands inside a shape: the
    house style has unbound pointers and brackets that clearly belong to the
    box they touch, and flagging those would bury the real collisions.
    """
    rel = {(a.get(k) or {}).get("elementId")
           for k in ("startBinding", "endBinding")}
    rel.discard(None)
    pts = points_of(a)
    if pts:
        for s in shapes:
            sb = boxes.get(s["id"])
            if sb and (contains(sb, pts[0], TOUCH_PAD)
                       or contains(sb, pts[-1], TOUCH_PAD)):
                rel.add(s["id"])
    return rel


def _geometry(live, by_id, boxes) -> list[dict]:
    out = []
    B = boxes.get
    texts = [e for e in live if e.get("type") == "text" and B(e.get("id"))]
    shapes = [e for e in live if e.get("type") in SHAPES and B(e.get("id"))]
    conns = [e for e in live if e.get("type") in CONNECTORS
             and len(points_of(e)) >= 2]

    # (a) text over text -------------------------------------------------
    for i, t1 in enumerate(texts):
        for t2 in texts[i + 1:]:
            b1, b2 = B(t1["id"]), B(t2["id"])
            ox, oy = overlap(b1, b2)
            if ox > TEXT_OVERLAP and oy > TEXT_OVERLAP:
                out.append(finding(
                    "text-over-text", ERROR, [t1["id"], t2["id"]],
                    f"{t1['id']} ({_snip(t1)}) overlaps {t2['id']} ({_snip(t2)}) "
                    f"by {ox:.0f}x{oy:.0f}px",
                    at=(max(b1[0], b2[0]), max(b1[1], b2[1]))))

    # (b) text over a shape it does not label ----------------------------
    for t in texts:
        tb = B(t["id"])
        for s in shapes:
            if s["id"] == t.get("containerId") or _is_zone(s):
                continue
            sb = B(s["id"])
            ox, oy = overlap(tb, sb)
            if ox > TEXT_OVERLAP and oy > TEXT_OVERLAP:
                out.append(finding(
                    "text-over-shape", ERROR, [t["id"], s["id"]],
                    f"{t['id']} ({_snip(t)}) sits on {s['id']}, which it does not "
                    f"label ({ox:.0f}x{oy:.0f}px)",
                    at=(max(tb[0], sb[0]), max(tb[1], sb[1]))))

    # (c) an arrow through a shape it is not attached to ------------------
    for a in conns:
        rel = _related_shapes(a, shapes, boxes)
        pts = points_of(a)
        for s in shapes:
            if s["id"] in rel or _is_zone(s):
                continue
            sb = inset(B(s["id"]), SHAPE_INSET)
            for i in range(len(pts) - 1):
                hit = seg_box_hit(pts[i], pts[i + 1], sb)
                if hit:
                    out.append(finding(
                        "arrow-through-shape", ERROR, [a["id"], s["id"]],
                        f"{a['id']} segment {i + 1} cuts through {s['id']}, which "
                        f"it is not attached to", at=hit))
                    break

    # (d) an arrow across someone else's text ----------------------------
    for a in conns:
        own = {r.get("id") for r in (a.get("boundElements") or [])}
        pts = points_of(a)
        for t in texts:
            if t["id"] in own or t.get("containerId") == a["id"]:
                continue
            tb = inset(B(t["id"]), TEXT_INSET)
            for i in range(len(pts) - 1):
                hit = seg_box_hit(pts[i], pts[i + 1], tb)
                if hit:
                    out.append(finding(
                        "arrow-over-text", ERROR, [a["id"], t["id"]],
                        f"{a['id']} segment {i + 1} crosses the label {t['id']} "
                        f"({_snip(t)})", at=hit))
                    break

    # (e) a label bound to an arrow --------------------------------------
    # The exporter draws the arrow stroke straight through a bound label, so
    # this is always wrong in the SVG however good it looks in the editor.
    for t in texts:
        c = by_id.get(t.get("containerId"))
        if c is None or c.get("type") not in CONNECTORS:
            continue
        out.append(finding(
            "label-bound-to-arrow", ERROR, [t["id"], c["id"]],
            f"{t['id']} ({_snip(t)}) is bound to arrow {c['id']}; the arrow "
            f"stroke is drawn through it on export - {BOUND_LABEL_FIX}",
            at=(B(t["id"])[0], B(t["id"])[1])))

    # (f) arrowheads piling up on one side of a shape --------------------
    groups: dict[tuple[str, str], list[tuple[str, tuple[float, float]]]] = {}
    for a in conns:
        if a.get("type") != "arrow" or a.get("endArrowhead", "arrow") is None:
            continue
        pts = points_of(a)
        tip = pts[-1]
        end = (a.get("endBinding") or {}).get("elementId")
        target = end if end in by_id else None
        if target is None:                      # unbound: whichever box it lands on
            for s in shapes:
                if contains(B(s["id"]), tip, TOUCH_PAD):
                    target = s["id"]
                    break
        tb = B(target) if target else None
        if tb is None:
            continue
        groups.setdefault((target, nearest_side(tip, tb)), []).append((a["id"], tip))
    for (target, side), arrows in sorted(groups.items()):
        if len(arrows) < 2:
            continue
        for cluster in _cluster(arrows, PILEUP_GAP):
            if len(cluster) < 2:
                continue
            ids = [c[0] for c in cluster]
            out.append(finding(
                "arrowhead-pileup", WARN, ids + [target],
                f"{len(ids)} arrowheads land within {PILEUP_GAP:.0f}px of each "
                f"other on the {side} of {target}: {', '.join(ids)}",
                at=cluster[0][1]))

    # (g) a label wider (or taller) than the shape holding it ------------
    for t in texts:
        c = by_id.get(t.get("containerId"))
        if c is None or c.get("type") not in SHAPES:
            continue
        cb = B(c["id"])
        if cb is None:
            continue
        size = float(t.get("fontSize", MIN_FONT))
        lines = _label_text(t).split("\n")
        need_w = max((len(ln) for ln in lines), default=0) * size * CHAR_W
        need_h = len(lines) * size * 1.25
        cw, ch = cb[2] - cb[0], cb[3] - cb[1]
        kind = c.get("type")
        share = 1.0 if kind in ("rectangle", "image") else (
            0.75 if kind == "ellipse" else 0.6)
        avail_w = cw - 2 * LABEL_PAD if share == 1.0 else cw * share
        avail_h = ch - 2 * LABEL_PAD if share == 1.0 else ch * share
        if need_w > avail_w:
            out.append(finding(
                "text-overflow", WARN, [t["id"], c["id"]],
                f"{t['id']} ({_snip(t)}) needs ~{need_w:.0f}px of width and "
                f"{c['id']} offers {avail_w:.0f}px, so it will overflow",
                at=(cb[0], cb[1])))
        elif need_h > avail_h:
            out.append(finding(
                "text-overflow", WARN, [t["id"], c["id"]],
                f"{t['id']} ({_snip(t)}) needs ~{need_h:.0f}px of height for "
                f"{len(lines)} lines and {c['id']} offers {avail_h:.0f}px, so it "
                f"will overflow", at=(cb[0], cb[1])))

    # (h) text sitting on a zone's outline -------------------------------
    # A zone is drawn to have things on top of it, so text well inside one is
    # fine and text clear of it is fine. Text that straddles the boundary is
    # not: the zone's stroke is drawn straight through the words, and no
    # z-order or opacity setting moves the line off them.
    for t in texts:
        tb = B(t["id"])
        for s in shapes:
            if not _is_zone(s) or s["id"] == t.get("containerId"):
                continue
            sb = B(s["id"])
            if s.get("type") == "ellipse":
                inside = all(pt_in_ellipse(p, ellipse_of(sb, ZONE_EDGE_TOL))
                             for p in corners(tb))
                clear = not box_hits_ellipse(tb, ellipse_of(sb, -ZONE_EDGE_TOL))
            else:
                inside = rect_inside(tb, sb, ZONE_EDGE_TOL)
                ox, oy = overlap(tb, inset(sb, ZONE_EDGE_TOL))
                clear = ox <= 0 or oy <= 0
            if inside or clear:
                continue
            out.append(finding(
                "text-over-outline", ERROR, [t["id"], s["id"]],
                f"{t['id']} ({_snip(t)}) crosses the outline of the zone "
                f"{s['id']}, so the zone stroke is drawn through the words",
                at=(tb[0], tb[1])))
    return out


def _cluster(items, gap: float):
    """Group (id, point) pairs whose points chain together within `gap`."""
    groups: list[list] = []
    for item in sorted(items, key=lambda it: (it[1][0], it[1][1])):
        for g in groups:
            if any(math.dist(item[1], other[1]) < gap for other in g):
                g.append(item)
                break
        else:
            groups.append([item])
    return groups


# --------------------------------------------------------------- reporting
def report_lines(name: str, findings: list[dict], count: int) -> list[str]:
    errors = [f for f in findings if f["severity"] == ERROR]
    warns = [f for f in findings if f["severity"] == WARN]
    lines = [f"{name}: {len(errors)} error(s), {len(warns)} warning(s), "
             f"{count} elements"]
    for f in errors + warns:
        where = f" at ({f['at'][0]:.0f},{f['at'][1]:.0f})" if f.get("at") else ""
        lines.append(f"  {f['severity'].upper():<5} {f['rule']:<21} "
                     f"{f['message']}{where}")
    if not findings:
        lines.append("  clean")
    return lines


def check_file(path: Path, write_json: bool = True) -> tuple[list[dict], int]:
    """Lint one scene file. Returns (findings, element count)."""
    path = Path(path)
    doc = json.loads(path.read_text())
    findings = lint_scene(doc)
    count = len([e for e in (doc.get("elements") or []) if not e.get("isDeleted")])
    if write_json:
        path.with_suffix(".lint.json").write_text(json.dumps({
            "source": str(path),
            "elements": count,
            "errors": sum(1 for f in findings if f["severity"] == ERROR),
            "warnings": sum(1 for f in findings if f["severity"] == WARN),
            "findings": findings,
        }, indent=1) + "\n")
    return findings, count


# --------------------------------------------------------------- png render
SVG_SIZE = re.compile(
    r'<svg[^>]*?\bwidth="([\d.]+)"[^>]*?\bheight="([\d.]+)"', re.S)


def render_png(svg: Path, png: Path, scale: int = 2) -> tuple[bool, str]:
    """Rasterise an exported SVG so a human (or the model) can look at it.

    Chromium via Playwright, because that is the renderer already installed for
    the dashboard tests and it draws the same SVG the dashboard shows.
    """
    svg, png = Path(svg), Path(png)
    if not svg.exists():
        return False, f"no SVG at {svg.name}; run tools/export_diagrams.py first"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "playwright is not installed; skipping the PNG"

    markup = svg.read_text()
    m = SVG_SIZE.search(markup)
    w, h = (float(m.group(1)), float(m.group(2))) if m else (1600.0, 1200.0)
    holder = svg.with_name(svg.stem + ".__render__.html")
    holder.write_text(
        "<style>html,body{margin:0;padding:0;background:#fff}"
        "svg{display:block}</style>\n" + markup)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(device_scale_factor=scale, viewport={
                    "width": max(320, min(4000, int(w) + 4)),
                    "height": max(320, min(4000, int(h) + 4))})
                page.goto(holder.resolve().as_uri())   # as_uri needs an absolute path
                page.locator("svg").first.screenshot(path=str(png))
            finally:
                browser.close()
    except Exception as exc:              # noqa: BLE001 - report, do not crash
        return False, f"could not render: {type(exc).__name__}: {exc}"
    finally:
        holder.unlink(missing_ok=True)
    return True, f"{png.name} at {scale}x"


# --------------------------------------------------------------- cli
def _default_study() -> Path:
    """The study folder jobscout uses, imported lazily so a plain path target
    works on a checkout where the app cannot be imported."""
    sys.path.insert(0, str(ROOT))
    from jobscout import settings

    return settings.study_dir()


def resolve(target: str, study) -> Path:
    """A slug or a path, either way the scene file it means."""
    p = Path(target)
    if p.suffix == ".excalidraw" or p.exists():
        return p
    return Path(study() if callable(study) else study) / "diagrams" / f"{target}.excalidraw"


def run(targets: list[str], study, png: bool = False) -> int:
    worst = 0
    for target in targets:
        src = resolve(target, study)
        if not src.exists():
            print(f"{target}: no scene file at {src}")
            worst = 1
            continue
        try:
            findings, count = check_file(src)
        except (OSError, ValueError) as exc:
            print(f"{src.stem}: could not read the scene - {exc}")
            worst = 1
            continue
        print("\n".join(report_lines(src.stem, findings, count)))
        if any(f["severity"] == ERROR for f in findings):
            worst = 1
        if png:
            ok, message = render_png(src.with_suffix(".svg"),
                                     src.with_suffix(".png"))
            print(f"  png: {message}" if ok else f"  png skipped: {message}")
    return worst


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("targets", nargs="+", help="topic slugs or .excalidraw paths")
    ap.add_argument("--study", type=Path, default=None,
                    help="study folder (default: the one jobscout uses)")
    ap.add_argument("--png", action="store_true",
                    help="also rasterise the sibling SVG to <slug>.png at 2x")
    args = ap.parse_args(argv)
    study = args.study if args.study is not None else _default_study
    return run(args.targets, study, png=args.png)


if __name__ == "__main__":
    raise SystemExit(main())
