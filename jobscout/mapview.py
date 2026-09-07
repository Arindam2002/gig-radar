"""The study map: the topic graph drawn as a force-directed picture.

One Streamlit custom component (v2) that takes the graph `jobscout.graph`
builds and lays it out as an SVG. The layout is a small hand-rolled
simulation - repulsion between every pair, a spring along every edge, a pull
toward the middle, and a hard separation pass that guarantees no two circles
overlap - run to convergence at render time. There is no animation and no
timer: the picture you get is the settled one.

Two properties matter more than prettiness here:

* **Deterministic.** Streamlit reruns the script on every click, so a layout
  seeded from anything but the node set itself would jump under your cursor.
  The seed is a hash of the sorted slugs and the only randomness is a seeded
  PRNG, so the same set of topics always lands in the same place.
* **Self-contained.** No CDN, no font, no fetch. The dashboard runs offline
  and inside Docker, and a map that needs the network is a map you cannot
  read on a plane. The SVG namespace is taken from the root element the
  browser already parsed rather than written out as a URL.

Interaction: hover for the topic's card, click to open its page, drag to pin
a node. A pin round-trips through `setStateValue("pins", ...)` into session
state and comes back down through `data.pins`, so it survives reruns.
"""
from __future__ import annotations

import math
from collections.abc import Callable

import streamlit as st

# The dashboard's muted palette, one colour per track. Kept here rather than
# in graph.py: the graph is data, this is how the data looks.
TRACK_COLORS = {
    "dsa": "#5ed4be",
    "backend": "#8b9cf7",
    "system-design": "#d2b478",
    "llm-infra": "#c497e6",
    "resume": "#94a3b8",
}
UNKNOWN_COLOR = "#64748b"

# Radius carries `words`, the only size signal a topic has. sqrt so a topic
# twice as long is not twice as wide; clamped so nothing vanishes or bullies
# the canvas.
R_MIN, R_MAX, R_SCALE = 9.0, 22.0, 0.75

LABEL_MAX = 28


def color_for(track: str | None) -> str:
    """The track's colour, or the grey every unlabelled topic shares."""
    return TRACK_COLORS.get((track or "").strip(), UNKNOWN_COLOR)


def radius_for(words) -> float:
    """Node radius from the topic's word count, sqrt-scaled and clamped."""
    try:
        w = max(0.0, float(words))
    except (TypeError, ValueError):
        w = 0.0
    return round(min(R_MAX, max(R_MIN, math.sqrt(w) * R_SCALE)), 2)


def label_for(title: str, limit: int = LABEL_MAX) -> str:
    """The label beside a node: the title, cut to something that fits."""
    t = " ".join((title or "").split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def seed_for(slugs) -> int:
    """A stable 32-bit seed for a set of topics.

    FNV-1a over the sorted slugs rather than Python's `hash`, which is salted
    per process: the layout has to be the same after a dashboard restart, not
    only within one run.
    """
    h = 0x811C9DC5
    for s in sorted(slugs):
        for b in f"{s}\n".encode():
            h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h or 1


def filter_graph(graph: dict, *, tracks=None, unstudied_only: bool = False) -> dict:
    """The visible slice of the graph.

    `tracks=None` keeps every track; a list (even an empty one) keeps exactly
    what it names. An edge survives only when both of its ends did, so the
    picture never grows a line into nowhere.
    """
    nodes = list(graph.get("nodes") or [])
    if tracks is not None:
        keep = set(tracks)
        nodes = [n for n in nodes if n.get("track") in keep]
    if unstudied_only:
        nodes = [n for n in nodes if not n.get("studied")]
    visible = {n["slug"] for n in nodes}
    edges = [e for e in (graph.get("edges") or [])
             if e.get("source") in visible and e.get("target") in visible]
    return {"nodes": nodes, "edges": edges}


def _clean_pins(pins, visible: set) -> dict:
    """Pins for topics that are actually on screen, as numbers.

    Whatever the browser last emitted comes back through session state, so a
    stale slug or a bad pair must not reach the layout.
    """
    out = {}
    for slug, xy in (pins or {}).items():
        if slug not in visible:
            continue
        try:
            x, y = float(xy[0]), float(xy[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            out[slug] = [x, y]
    return out


def payload(graph: dict, pins=None) -> dict:
    """The `data` the component renders: nodes, edges, pins and the seed.

    Everything the drawing needs is computed here (colour, radius, label) so
    the JavaScript only lays out and paints, and so the choices are testable
    without a browser. Nodes and edges are sorted, which is what makes the
    seed and therefore the layout reproducible.
    """
    nodes = sorted(graph.get("nodes") or [], key=lambda n: n["slug"])
    visible = {n["slug"] for n in nodes}
    out_nodes = []
    for n in nodes:
        track = n.get("track") or "unknown"
        out_nodes.append({
            "slug": n["slug"],
            "title": n.get("title") or n["slug"],
            "label": label_for(n.get("title") or n["slug"]),
            "track": track,
            "tags": list(n.get("tags") or []),
            "words": int(n.get("words") or 0),
            "studied": bool(n.get("studied")),
            "notes": int(n.get("notes") or 0),
            "color": color_for(track),
            "r": radius_for(n.get("words")),
        })
    seen = set()
    out_edges = []
    for e in graph.get("edges") or []:
        s, t = e.get("source"), e.get("target")
        if s not in visible or t not in visible or s == t:
            continue
        key = (s, t) if s < t else (t, s)
        if key in seen:                 # one line per pair, whatever its kind
            continue
        seen.add(key)
        out_edges.append({"source": s, "target": t, "kind": e.get("kind", "link")})
    out_edges.sort(key=lambda e: (e["source"], e["target"]))
    return {"nodes": out_nodes, "edges": out_edges,
            "pins": _clean_pins(pins, visible), "seed": seed_for(visible)}


_HTML = """
<div class="jsm-wrap">
  <svg class="jsm-svg" role="img" aria-label="Study topic map"></svg>
  <div class="jsm-tip" hidden></div>
</div>
"""

_CSS = """
.jsm-wrap {position: relative; width: 100%;}
.jsm-svg {display: block; width: 100%; height: 560px;
  background: rgba(148,163,184,.04); border-radius: 14px;
  border: 1px solid var(--st-border-color, rgba(148,163,184,.14));
  touch-action: none; user-select: none;}
.jsm-edge {stroke: rgba(148,163,184,.30); stroke-width: 1;}
.jsm-edge.jsm-related {stroke: rgba(148,163,184,.45);}
.jsm-node {cursor: pointer;}
.jsm-node:hover {filter: brightness(1.25);}
.jsm-badge {pointer-events: none;}
.jsm-badge-text {pointer-events: none; font-size: 8px; font-weight: 700;
  text-anchor: middle; fill: var(--st-background-color, #12151c);}
/* the halo is what keeps a label readable where it crosses an edge */
.jsm-label {pointer-events: none; font-size: 11px;
  fill: var(--st-text-color, #d7dce5); fill-opacity: .82;
  paint-order: stroke; stroke: var(--st-background-color, #12151c);
  stroke-width: 3px; stroke-linejoin: round; stroke-opacity: .85;}
.jsm-tip {position: absolute; z-index: 60; pointer-events: none;
  max-width: 280px; padding: 7px 10px; border-radius: 10px; font-size: .78rem;
  line-height: 1.45; color: var(--st-text-color, #d7dce5);
  background: var(--st-secondary-background-color, #1a1e27);
  border: 1px solid var(--st-border-color, rgba(148,163,184,.25));
  box-shadow: 0 8px 24px rgba(0,0,0,.45);}
.jsm-tip[hidden] {display: none;}
.jsm-tip-title {font-weight: 650; margin-bottom: 2px;}
.jsm-tip-meta {opacity: .6;}
"""

_JS = """
export default function (component) {
  const { data, parentElement, setStateValue } = component
  const wrap = parentElement.querySelector(".jsm-wrap")
  const svg = parentElement.querySelector(".jsm-svg")
  const tip = parentElement.querySelector(".jsm-tip")
  if (!wrap || !svg) return
  const NS = svg.namespaceURI          // taken from the DOM, never spelled out

  const nodes = (data && data.nodes) || []
  const edges = (data && data.edges) || []
  const pins = (data && data.pins) || {}
  const n = nodes.length
  svg.replaceChildren()
  if (!n) return

  // ── seeded PRNG: same topics, same picture, run after run ──
  function mulberry32(a) {
    return function () {
      a = (a + 0x6D2B79F5) | 0
      let t = Math.imul(a ^ (a >>> 15), 1 | a)
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296
    }
  }
  const rnd = mulberry32((data.seed | 0) || 1)

  const W = 900, H = 520, CX = W / 2, CY = H / 2
  const idx = {}
  nodes.forEach((nd, i) => { idx[nd.slug] = i })

  // initial ring placement, jittered from the seed so symmetric graphs do
  // not sit in a force stalemate. Every node draws, pinned or not, so the
  // sequence does not shift when you pin one.
  const P = nodes.map((nd, i) => {
    const jitterA = rnd(), jitterR = rnd()
    const ang = 2 * Math.PI * (i + 0.35 * jitterA) / n
    const rad = 45 + (0.62 * Math.min(W, H) / 2) *
                Math.sqrt((i + 0.35 + 0.3 * jitterR) / n)
    return { x: CX + rad * Math.cos(ang), y: CY + rad * Math.sin(ang),
             r: nd.r || 9, fixed: false }
  })
  for (const nd of nodes) {
    const p = pins[nd.slug]
    const i = idx[nd.slug]
    if (Array.isArray(p) && p.length === 2 && isFinite(p[0]) && isFinite(p[1])) {
      P[i].x = +p[0]; P[i].y = +p[1]; P[i].fixed = true
    }
  }

  const L = []
  for (const e of edges) {
    const a = idx[e.source], b = idx[e.target]
    if (a === undefined || b === undefined || a === b) continue
    L.push([a, b, e.kind === "related"])
  }

  // ── the simulation ──
  const REP = 24000, SPRING = 0.055, GAP = 66, PULL = 0.014
  const PAD = 7, ITER = 300, MAXSTEP = 34

  function separate() {
    let moved = false
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = P[j].x - P[i].x, dy = P[j].y - P[i].y
        let d = Math.sqrt(dx * dx + dy * dy)
        const min = P[i].r + P[j].r + PAD
        if (d >= min) continue
        if (d < 1e-6) { dx = 1 + i * 1e-3; dy = j * 1e-3; d = Math.sqrt(dx * dx + dy * dy) }
        const push = (min - d) / 2
        const ux = (dx / d) * push, uy = (dy / d) * push
        if (!P[i].fixed) { P[i].x -= ux; P[i].y -= uy; moved = true }
        if (!P[j].fixed) { P[j].x += ux; P[j].y += uy; moved = true }
      }
    }
    return moved
  }

  const fx = new Float64Array(n), fy = new Float64Array(n)
  for (let it = 0; it < ITER; it++) {
    const alpha = Math.pow(0.006, it / (ITER - 1))
    fx.fill(0); fy.fill(0)
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = P[i].x - P[j].x, dy = P[i].y - P[j].y
        let d2 = dx * dx + dy * dy
        if (d2 < 1e-6) { dx = 0.01 + i * 1e-4; dy = 0.013 + j * 1e-4; d2 = dx * dx + dy * dy }
        const d = Math.sqrt(d2), f = REP / d2
        const ux = (dx / d) * f, uy = (dy / d) * f
        fx[i] += ux; fy[i] += uy
        fx[j] -= ux; fy[j] -= uy
      }
    }
    for (const [a, b] of L) {
      const dx = P[b].x - P[a].x, dy = P[b].y - P[a].y
      const d = Math.max(0.01, Math.sqrt(dx * dx + dy * dy))
      const f = (d - (P[a].r + P[b].r + GAP)) * SPRING
      const ux = (dx / d) * f, uy = (dy / d) * f
      fx[a] += ux; fy[a] += uy
      fx[b] -= ux; fy[b] -= uy
    }
    for (let i = 0; i < n; i++) {
      fx[i] += (CX - P[i].x) * PULL
      fy[i] += (CY - P[i].y) * PULL
    }
    for (let i = 0; i < n; i++) {
      if (P[i].fixed) continue
      let dx = fx[i] * alpha, dy = fy[i] * alpha
      const m = Math.sqrt(dx * dx + dy * dy)
      if (m > MAXSTEP) { dx = (dx / m) * MAXSTEP; dy = (dy / m) * MAXSTEP }
      P[i].x += dx; P[i].y += dy
    }
    separate()
  }
  // the guarantee, not the tendency: circles must not overlap
  for (let k = 0; k < 400; k++) if (!separate()) break
  for (let i = 0; i < n; i++) {
    P[i].x = Math.round(P[i].x * 100) / 100
    P[i].y = Math.round(P[i].y * 100) / 100
  }

  // ── paint ──
  const el = (tag, attrs) => {
    const e = document.createElementNS(NS, tag)
    for (const k in attrs) e.setAttribute(k, attrs[k])
    return e
  }

  // Labels go on whichever side of their node is clearer, falling back to
  // "away from the middle" when both sides are equally blocked, so they run
  // out of the drawing rather than across it. ~6.2px per character at
  // font-size 11 is close enough to reserve room for without measuring.
  let sumX = 0
  for (let i = 0; i < n; i++) {
    sumX += P[i].x
    P[i].lw = ((nodes[i].label || nodes[i].slug).length * 6.2) + 10
  }
  const midX = sumX / n
  function blocked(i, right) {
    const x0 = right ? P[i].x + P[i].r + 6 : P[i].x - P[i].r - 6 - P[i].lw
    const x1 = x0 + P[i].lw
    let pen = 0
    for (let j = 0; j < n; j++) {
      if (j === i || Math.abs(P[j].y - P[i].y) > P[j].r + 9) continue
      const ox = Math.min(x1, P[j].x + P[j].r) - Math.max(x0, P[j].x - P[j].r)
      if (ox > 0) pen += ox
    }
    return pen
  }
  for (let i = 0; i < n; i++) {
    const pr = blocked(i, true), pl = blocked(i, false)
    P[i].right = pr < pl ? true : (pl < pr ? false : (n < 2 || P[i].x >= midX))
  }

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (let i = 0; i < n; i++) {
    const lx = P[i].right ? P[i].x + P[i].r + 6 + P[i].lw : P[i].x - P[i].r - 6 - P[i].lw
    minX = Math.min(minX, P[i].x - P[i].r, lx); maxX = Math.max(maxX, P[i].x + P[i].r, lx)
    minY = Math.min(minY, P[i].y - P[i].r); maxY = Math.max(maxY, P[i].y + P[i].r)
  }
  const M = 26
  svg.setAttribute("viewBox",
    [minX - M, minY - M, (maxX - minX) + M * 2, (maxY - minY) + M * 2]
      .map(v => Math.round(v * 100) / 100).join(" "))

  const gEdges = el("g", { class: "jsm-edges" })
  const gNodes = el("g", { class: "jsm-nodes" })
  svg.appendChild(gEdges)
  svg.appendChild(gNodes)

  const lines = L.map(([a, b, related]) => {
    const ln = el("line", {
      class: "jsm-edge" + (related ? " jsm-related" : ""),
      x1: P[a].x, y1: P[a].y, x2: P[b].x, y2: P[b].y,
    })
    gEdges.appendChild(ln)
    return { ln, a, b }
  })

  const parts = nodes.map((nd, i) => {
    const g = el("g", { class: "jsm-item" })
    const c = el("circle", {
      class: "jsm-node", "data-slug": nd.slug, cx: P[i].x, cy: P[i].y, r: P[i].r,
      fill: nd.color, "fill-opacity": "0.85", stroke: nd.color,
      "stroke-width": nd.studied ? "2" : "1.5",
      "stroke-opacity": nd.studied ? "1" : "0.55",
    })
    if (!nd.studied) c.setAttribute("stroke-dasharray", "3 3")
    g.appendChild(c)

    let badge = null, badgeText = null
    if (nd.notes > 0) {
      badge = el("circle", {
        class: "jsm-badge", cx: P[i].x + P[i].r * 0.78, cy: P[i].y - P[i].r * 0.78,
        r: 6.5, fill: nd.color, stroke: "rgba(0,0,0,.35)", "stroke-width": "1",
      })
      badgeText = el("text", {
        class: "jsm-badge-text", x: P[i].x + P[i].r * 0.78,
        y: P[i].y - P[i].r * 0.78 + 3,
      })
      badgeText.textContent = nd.notes > 9 ? "9+" : String(nd.notes)
      g.appendChild(badge)
      g.appendChild(badgeText)
    }

    const label = el("text", {
      class: "jsm-label", y: P[i].y + 4,
      x: P[i].right ? P[i].x + P[i].r + 6 : P[i].x - P[i].r - 6,
      "text-anchor": P[i].right ? "start" : "end",
    })
    label.textContent = nd.label || nd.slug
    g.appendChild(label)
    gNodes.appendChild(g)
    return { c, badge, badgeText, label }
  })

  function place(i) {
    const p = P[i], q = parts[i], nd = nodes[i]
    q.c.setAttribute("cx", p.x); q.c.setAttribute("cy", p.y)
    if (q.badge) {
      q.badge.setAttribute("cx", p.x + p.r * 0.78)
      q.badge.setAttribute("cy", p.y - p.r * 0.78)
      q.badgeText.setAttribute("x", p.x + p.r * 0.78)
      q.badgeText.setAttribute("y", p.y - p.r * 0.78 + 3)
    }
    q.label.setAttribute("x", p.right ? p.x + p.r + 6 : p.x - p.r - 6)
    q.label.setAttribute("y", p.y + 4)
    void nd
    for (const { ln, a, b } of lines) {
      if (a === i) { ln.setAttribute("x1", p.x); ln.setAttribute("y1", p.y) }
      if (b === i) { ln.setAttribute("x2", p.x); ln.setAttribute("y2", p.y) }
    }
  }

  // ── tooltip ──
  function showTip(nd, ev) {
    if (!tip) return
    const t = document.createElement("div")
    t.className = "jsm-tip-title"
    t.textContent = nd.title
    const meta = document.createElement("div")
    meta.className = "jsm-tip-meta"
    const bits = [nd.track, nd.studied ? "studied" : "not studied yet"]
    if (nd.notes) bits.push(nd.notes + (nd.notes === 1 ? " note" : " notes"))
    meta.textContent = bits.join(" · ")
    tip.replaceChildren(t, meta)
    if (nd.tags && nd.tags.length) {
      const tg = document.createElement("div")
      tg.className = "jsm-tip-meta"
      tg.textContent = nd.tags.join(", ")
      tip.appendChild(tg)
    }
    tip.hidden = false
    const w = wrap.getBoundingClientRect()
    const x = Math.min(Math.max(0, ev.clientX - w.left + 14), w.width - tip.offsetWidth - 4)
    tip.style.left = Math.max(0, x) + "px"
    tip.style.top = Math.max(0, ev.clientY - w.top + 16) + "px"
  }
  function hideTip() { if (tip) { tip.hidden = true } }

  // ── drag to pin, click to open ──
  function toLayout(ev) {
    const box = svg.getBoundingClientRect()
    const vb = svg.viewBox.baseVal
    if (!box.width || !box.height) return null
    // preserveAspectRatio is the default (meet, centred), so the drawing is
    // uniformly scaled and letterboxed inside the element
    const s = Math.min(box.width / vb.width, box.height / vb.height)
    const offX = (box.width - vb.width * s) / 2
    const offY = (box.height - vb.height * s) / 2
    return [vb.x + (ev.clientX - box.left - offX) / s,
            vb.y + (ev.clientY - box.top - offY) / s]
  }

  let drag = null, justDragged = false

  const onOver = (ev) => {
    const c = ev.target.closest && ev.target.closest("circle.jsm-node")
    if (!c || drag) { if (!drag) hideTip(); return }
    const i = idx[c.dataset.slug]
    if (i !== undefined) showTip(nodes[i], ev)
  }
  const onDown = (ev) => {
    const c = ev.target.closest && ev.target.closest("circle.jsm-node")
    if (!c || ev.button !== 0) return
    const i = idx[c.dataset.slug]
    if (i === undefined) return
    ev.preventDefault()
    drag = { i, moved: false }
    hideTip()
  }
  const onMove = (ev) => {
    if (!drag) return
    const pt = toLayout(ev)
    if (!pt) return
    drag.moved = true
    P[drag.i].x = Math.round(pt[0] * 100) / 100
    P[drag.i].y = Math.round(pt[1] * 100) / 100
    place(drag.i)
  }
  const onUp = () => {
    if (!drag) return
    const d = drag
    drag = null
    if (!d.moved) return
    justDragged = true
    setTimeout(() => { justDragged = false }, 0)
    const next = {}
    for (const k in pins) next[k] = pins[k]
    next[nodes[d.i].slug] = [P[d.i].x, P[d.i].y]
    setStateValue("pins", next)
  }
  const onClick = (ev) => {
    if (justDragged) return
    const c = ev.target.closest && ev.target.closest("circle.jsm-node")
    if (!c) return
    const slug = c.dataset.slug
    if (slug) window.location.assign("study?topic=" + encodeURIComponent(slug))
  }

  svg.addEventListener("mousemove", onOver)
  svg.addEventListener("mouseleave", hideTip)
  svg.addEventListener("mousedown", onDown)
  svg.addEventListener("click", onClick)
  document.addEventListener("mousemove", onMove)
  document.addEventListener("mouseup", onUp)

  return () => {
    document.removeEventListener("mousemove", onMove)
    document.removeEventListener("mouseup", onUp)
  }
}
"""

_MAP = st.components.v2.component(
    "jobscout_study_map",
    html=_HTML,
    css=_CSS,
    js=_JS,
    # not isolated: the map should inherit the dashboard's fonts and theme
    # variables, the same reason the reader is not isolated either
    isolate_styles=False,
)


def study_map(graph: dict, *, pins=None, key: str,
              on_pins_change: Callable[[], None] | None = None):
    """Draw the graph. `pins` is {slug: [x, y]} as the page last emitted it;
    read it back from st.session_state[key].pins BEFORE mounting."""
    return _MAP(
        key=key,
        data=payload(graph, pins),
        on_pins_change=on_pins_change or (lambda: None),
    )
