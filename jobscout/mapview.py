"""The study map: the topic graph drawn as a force-directed picture.

One Streamlit custom component (v2) that takes the graph `jobscout.graph`
builds and lays it out as an SVG. The layout is a small hand-rolled
simulation - repulsion between every pair, a spring along every edge, a pull
toward the middle, and a separation pass that keeps circles off each other -
and it runs twice, for two different reasons.

* **Once, synchronously, before the first paint.** 300 iterations to
  convergence, from a seeded ring. That result is the *layout of record*: it
  is written to `data-x0`/`data-y0` on every node and never recomputed, and
  it is what the drawing is framed around.
* **Then continuously, on `requestAnimationFrame`.** The same forces, now
  velocity-based, with an `alpha` that decays toward a small floor instead of
  to zero, plus a per-node "breathing" wander of two or three pixels whose
  phase is hashed from the slug. Nothing on this canvas is ever quite still,
  a drag ripples through the neighbourhood and springs back, and the loop
  sleeps (skipping the O(n^2) pass) once nothing is moving under its own
  steam. `cx`/`cy` therefore drift; `data-x0`/`data-y0` do not.

Three properties matter more than prettiness here:

* **Deterministic.** Streamlit reruns the script on every click, so a layout
  seeded from anything but the node set itself would jump under your cursor.
  The seed is a hash of the sorted slugs and the only randomness is a seeded
  PRNG, so the same set of topics always settles in the same place.
* **Continuous across reruns.** A filter change is a fresh mount with a new
  node set. Live positions and the drift clock are stashed on the host
  element, so surviving topics slide to the new answer and only genuinely new
  ones appear (fading in) - the map never teleports.
* **Self-contained.** No CDN, no font, no fetch. The dashboard runs offline
  and inside Docker, and a map that needs the network is a map you cannot
  read on a plane. The SVG namespace is taken from the root element the
  browser already parsed rather than written out as a URL.

Interaction: hover lights a topic and its neighbours and dims the rest, click
opens its page, drag moves it with live physics and pins it where you let go,
double-click unpins. A pin round-trips through `setStateValue("pins", ...)`
into session state and comes back down through `data.pins`, so it survives
reruns.

`prefers-reduced-motion: reduce` turns all of that off: the settle runs, the
picture is painted once, and no frame loop starts.
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
.jsm-edge {stroke: rgba(148,163,184,.30); stroke-width: 1;
  transition: opacity .2s ease, stroke-width .2s ease;}
.jsm-edge.jsm-related {stroke: rgba(148,163,184,.45);}
.jsm-node {cursor: pointer;}
.jsm-node:hover {filter: brightness(1.25);}
.jsm-item {transition: opacity .2s ease;}
/* hover focus: the node and everything it touches stay lit, the rest of the
   map falls back. Opacity only, so nothing here fights the per-frame writes
   the simulation makes to cx/cy/r. */
.jsm-svg.jsm-focusing .jsm-item {opacity: .2;}
.jsm-svg.jsm-focusing .jsm-item.jsm-lit {opacity: 1;}
.jsm-svg.jsm-focusing .jsm-edge {opacity: .12;}
.jsm-svg.jsm-focusing .jsm-edge.jsm-lit {opacity: 1; stroke-width: 1.7;
  stroke: var(--st-text-color, #d7dce5); stroke-opacity: .42;}
.jsm-item.jsm-hot circle.jsm-node {filter: brightness(1.35);}
/* a pinned topic stops drifting, so it needs to look deliberate rather than
   stuck: a heavier ring, and a double-click lets it go again */
.jsm-item.jsm-pinned circle.jsm-node {stroke-width: 2.6; stroke-opacity: 1;
  stroke-dasharray: none;}
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
@media (prefers-reduced-motion: reduce) {
  .jsm-item, .jsm-edge {transition: none;}
}
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
  svg.classList.remove("jsm-focusing")
  if (!n) return

  // Someone who asked the OS for less motion gets the old map: solved once,
  // painted once, and then left completely alone.
  const still = !!(window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches)

  // ── seeded PRNG: same topics, same picture, run after run ──
  function mulberry32(a) {
    return function () {
      a = (a + 0x6D2B79F5) | 0
      let t = Math.imul(a ^ (a >>> 15), 1 | a)
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296
    }
  }
  // FNV-1a over the slug, the same shape as the Python seed. The drift is
  // keyed to the topic rather than to its index, so filtering the map does
  // not re-roll everybody's wander.
  function hash32(s) {
    let h = 0x811C9DC5
    for (let i = 0; i < s.length; i++) {
      h = Math.imul(h ^ s.charCodeAt(i), 0x01000193) >>> 0
    }
    return h || 1
  }
  const rnd = mulberry32((data.seed | 0) || 1)

  const TAU = Math.PI * 2
  const W = 900, H = 520, CX = W / 2, CY = H / 2
  const idx = {}
  nodes.forEach((nd, i) => { idx[nd.slug] = i })

  // initial ring placement, jittered from the seed so symmetric graphs do
  // not sit in a force stalemate. Every node draws, pinned or not, so the
  // sequence does not shift when you pin one.
  const S = nodes.map((nd, i) => {
    const jitterA = rnd(), jitterR = rnd()
    const ang = TAU * (i + 0.35 * jitterA) / n
    const rad = 45 + (0.62 * Math.min(W, H) / 2) *
                Math.sqrt((i + 0.35 + 0.3 * jitterR) / n)
    return { x: CX + rad * Math.cos(ang), y: CY + rad * Math.sin(ang),
             vx: 0, vy: 0, r: nd.r || 9, fixed: false }
  })
  for (const nd of nodes) {
    const p = pins[nd.slug]
    const i = idx[nd.slug]
    if (Array.isArray(p) && p.length === 2 && isFinite(p[0]) && isFinite(p[1])) {
      S[i].x = +p[0]; S[i].y = +p[1]; S[i].fixed = true
    }
  }

  const L = []
  const adj = nodes.map(() => [])
  for (const e of edges) {
    const a = idx[e.source], b = idx[e.target]
    if (a === undefined || b === undefined || a === b) continue
    L.push([a, b, e.kind === "related"])
    adj[a].push(b); adj[b].push(a)
  }

  // ── the force model ──
  // One model, two drivers: a synchronous solve that produces the layout of
  // record, and a frame loop that keeps the same forces alive afterwards.
  const REP = 24000, SPRING = 0.055, GAP = 66, PULL = 0.014
  const PAD = 7, ITER = 300, MAXSTEP = 34
  // the live half: `alpha` scales every force and decays toward a floor
  // rather than to zero, and velocity carries between frames with heavy
  // damping. ALPHA_DECAY is per 60Hz frame, so ~0.955^120 - about two
  // seconds - is what "settled" means here.
  const ALPHA_FLOOR = 0.02, ALPHA_DECAY = 0.955
  const ACC = 0.35, VDECAY = 0.72, MAXV = 26
  const fx = new Float64Array(n), fy = new Float64Array(n)

  // O(n^2). At the sizes a study base reaches (tens of topics, a hundred at
  // the outside) that is a few thousand pairs a frame, which is nothing; the
  // frame loop skips the whole pass once the graph is asleep anyway.
  function accumulate(Q) {
    fx.fill(0); fy.fill(0)
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = Q[i].x - Q[j].x, dy = Q[i].y - Q[j].y
        let d2 = dx * dx + dy * dy
        if (d2 < 1e-6) { dx = 0.01 + i * 1e-4; dy = 0.013 + j * 1e-4; d2 = dx * dx + dy * dy }
        const d = Math.sqrt(d2), f = REP / d2
        const ux = (dx / d) * f, uy = (dy / d) * f
        fx[i] += ux; fy[i] += uy
        fx[j] -= ux; fy[j] -= uy
      }
    }
    for (const [a, b] of L) {
      const dx = Q[b].x - Q[a].x, dy = Q[b].y - Q[a].y
      const d = Math.max(0.01, Math.sqrt(dx * dx + dy * dy))
      const f = (d - (Q[a].r + Q[b].r + GAP)) * SPRING
      const ux = (dx / d) * f, uy = (dy / d) * f
      fx[a] += ux; fy[a] += uy
      fx[b] -= ux; fy[b] -= uy
    }
    for (let i = 0; i < n; i++) {
      fx[i] += (CX - Q[i].x) * PULL
      fy[i] += (CY - Q[i].y) * PULL
    }
  }

  // positional collision. `k` is 1 for the settle, where overlap is a
  // guarantee, and softer in the frame loop, where a hard shove would read
  // as a twitch.
  function separate(Q, k) {
    let moved = false
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = Q[j].x - Q[i].x, dy = Q[j].y - Q[i].y
        let d = Math.sqrt(dx * dx + dy * dy)
        const min = Q[i].r + Q[j].r + PAD
        if (d >= min) continue
        if (d < 1e-6) { dx = 1 + i * 1e-3; dy = j * 1e-3; d = Math.sqrt(dx * dx + dy * dy) }
        const push = ((min - d) / 2) * k
        const ux = (dx / d) * push, uy = (dy / d) * push
        if (!Q[i].fixed) { Q[i].x -= ux; Q[i].y -= uy; moved = true }
        if (!Q[j].fixed) { Q[j].x += ux; Q[j].y += uy; moved = true }
      }
    }
    return moved
  }

  // one tick of the live model. `s` is the frame's share of 1/60s, so the
  // same function drives the frame loop and the synchronous settle below.
  function integrate(Q, a, s) {
    accumulate(Q)
    const decay = Math.pow(VDECAY, s)
    let hot = 0
    for (let i = 0; i < n; i++) {
      const p = Q[i]
      if (p.fixed) { p.vx = 0; p.vy = 0; continue }
      p.vx = (p.vx + fx[i] * ACC * a * s) * decay
      p.vy = (p.vy + fy[i] * ACC * a * s) * decay
      const m = Math.hypot(p.vx, p.vy)
      if (m > MAXV) { p.vx = (p.vx / m) * MAXV; p.vy = (p.vy / m) * MAXV }
      p.x += p.vx * s; p.y += p.vy * s
      if (m > hot) hot = m
    }
    separate(Q, 0.5)
    return hot
  }

  // ── the layout of record ──
  // Run to convergence before the first paint and never recomputed after:
  // this is what `data-x0`/`data-y0` report and what the drawing is framed
  // around. The live coordinates wander off it by a few pixels; the settled
  // ones are the answer to "where does this topic live".
  for (let it = 0; it < ITER; it++) {
    const alpha0 = Math.pow(0.006, it / (ITER - 1))
    accumulate(S)
    for (let i = 0; i < n; i++) {
      if (S[i].fixed) continue
      let dx = fx[i] * alpha0, dy = fy[i] * alpha0
      const m = Math.sqrt(dx * dx + dy * dy)
      if (m > MAXSTEP) { dx = (dx / m) * MAXSTEP; dy = (dy / m) * MAXSTEP }
      S[i].x += dx; S[i].y += dy
    }
    separate(S, 1)
  }
  // Then a second pass under the *live* dynamics, run here rather than on
  // screen. Annealing gets close but freezes at a low alpha with real forces
  // still on the nodes, and the frame loop would spend its first minute
  // quietly walking the picture somewhere else - alpha sets how fast the
  // simulation answers, not where it is going, and near the floor "slowly"
  // means a time constant measured in thousands of frames. Held at full
  // alpha this converges on the zero-force point in a few hundred ticks, and
  // that point is where the frame loop then sits: the layout of record is
  // the layout you see, and `data-x0` is not a fiction about it.
  for (let it = 0; it < 900; it++) if (integrate(S, 1, 1) < 0.004) break
  // the guarantee, not the tendency: circles must not overlap
  for (let k = 0; k < 400; k++) if (!separate(S, 1)) break
  const r2 = (v) => Math.round(v * 100) / 100
  for (let i = 0; i < n; i++) {
    S[i].x = r2(S[i].x); S[i].y = r2(S[i].y); S[i].vx = 0; S[i].vy = 0
  }

  // ── live positions ──
  // A filter change is a new mount with a new node set. Nodes that survived
  // it start from wherever they were left and slide to the new answer; only
  // genuinely new ones appear at their settled spot, and those fade in.
  const carried = (parentElement.__jsmPos && typeof parentElement.__jsmPos === "object")
    ? parentElement.__jsmPos : {}
  let T = +parentElement.__jsmT || 0
  const ok = (q) => Array.isArray(q) && isFinite(q[0]) && isFinite(q[1])
  // A first mount has nothing to carry, so the graph would appear fully
  // formed and merely breathe. It opens instead - see `opening` below.
  const fresh = !still && !nodes.some((nd) => ok(carried[nd.slug]))
  let bx = 0, by = 0
  for (let i = 0; i < n; i++) { bx += S[i].x; by += S[i].y }
  bx /= n; by /= n
  const P = nodes.map((nd, i) => {
    const q = carried[nd.slug]
    const warm = !still && ok(q)
    // the breathing: two slow sines per axis, so the path is a lazy Lissajous
    // rather than a metronome, with amplitude and phase fixed by the slug
    const w = mulberry32(hash32(nd.slug))
    return {
      x: warm ? +q[0] : S[i].x, y: warm ? +q[1] : S[i].y,
      rx: 0, ry: 0, vx: 0, vy: 0, r: S[i].r, fixed: S[i].fixed,
      lw: 0, right: false, born: warm ? 1 : 0,
      hold: 0, hx: 0, hy: 0,
      ax: 2.0 + 1.6 * w(), ay: 1.8 + 1.6 * w(),
      f1: 0.52 + 0.46 * w(), f2: 0.19 + 0.22 * w(),
      f3: 0.49 + 0.46 * w(), f4: 0.17 + 0.22 * w(),
      p1: TAU * w(), p2: TAU * w(), p3: TAU * w(), p4: TAU * w(),
    }
  })
  // a hidden tab never runs the loop that grows nodes in, so a map mounted
  // in the background must arrive fully drawn (it would otherwise show only
  // edges until the tab is focused)
  if (still || document.hidden) for (let i = 0; i < n; i++) { P[i].born = 1 }

  const wobX = (p) => p.ax * (0.72 * Math.sin(T * p.f1 + p.p1) +
                              0.28 * Math.sin(T * p.f2 + p.p2))
  const wobY = (p) => p.ay * (0.72 * Math.sin(T * p.f3 + p.p3) +
                              0.28 * Math.sin(T * p.f4 + p.p4))

  // A hovered node stops breathing. `hold` freezes its drift at whatever it
  // was the instant the pointer arrived - not at zero, which would slide the
  // circle out from under the cursor - and eases back in when you leave. It
  // is three pixels of wander, so this is not about being able to hit the
  // thing so much as about it not squirming while you read its card.
  // a pinned node is parked exactly where it was dropped: no wander either
  const offX = (p) => (still || p.fixed ? 0 : wobX(p) * (1 - p.hold) + p.hx * p.hold)
  const offY = (p) => (still || p.fixed ? 0 : wobY(p) * (1 - p.hold) + p.hy * p.hold)

  // The opening. The map arrives a tenth smaller than it is and expands to
  // full size over about a second, which is the "settling" a reader sees on
  // first load. It happens at paint time, on top of the physics rather than
  // inside it: displacing the actual nodes and asking the springs to push
  // them back would take a minute to converge at the alpha floor, and would
  // land somewhere slightly other than the layout of record. This cannot -
  // it multiplies by a factor that decays to exactly 1.
  const OPEN = 0.1
  let opening = fresh ? 1 : 0
  const spread = () => 1 - OPEN * opening

  // ── paint ──
  const el = (tag, attrs) => {
    const e = document.createElementNS(NS, tag)
    for (const k in attrs) e.setAttribute(k, attrs[k])
    return e
  }

  // Labels go on whichever side of their node is clearer, falling back to
  // "away from the middle" when both sides are equally blocked, so they run
  // out of the drawing rather than across it. Decided from the settled
  // layout and then left alone: a label that flipped sides mid-drift would
  // be the one thing on this canvas that actually looked broken. ~6.2px per
  // character at font-size 11 is close enough to reserve room for without
  // measuring.
  let sumX = 0
  for (let i = 0; i < n; i++) {
    sumX += S[i].x
    P[i].lw = ((nodes[i].label || nodes[i].slug).length * 6.2) + 10
  }
  const midX = sumX / n
  function blocked(i, right) {
    const x0 = right ? S[i].x + S[i].r + 6 : S[i].x - S[i].r - 6 - P[i].lw
    const x1 = x0 + P[i].lw
    let pen = 0
    for (let j = 0; j < n; j++) {
      if (j === i || Math.abs(S[j].y - S[i].y) > S[j].r + 9) continue
      const ox = Math.min(x1, S[j].x + S[j].r) - Math.max(x0, S[j].x - S[j].r)
      if (ox > 0) pen += ox
    }
    return pen
  }
  for (let i = 0; i < n; i++) {
    const pr = blocked(i, true), pl = blocked(i, false)
    P[i].right = pr < pl ? true : (pl < pr ? false : (n < 2 || S[i].x >= midX))
  }

  // The frame is the settled layout plus room for the drift. When nodes are
  // sliding in from an older, wider picture the box starts around both and
  // eases down to the settled one, so nothing is ever clipped mid-move.
  const M = 26, DRIFT = 6
  function box(Q, into) {
    for (let i = 0; i < n; i++) {
      const lx = P[i].right ? Q[i].x + Q[i].r + 6 + P[i].lw
                            : Q[i].x - Q[i].r - 6 - P[i].lw
      into[0] = Math.min(into[0], Q[i].x - Q[i].r, lx)
      into[1] = Math.min(into[1], Q[i].y - Q[i].r)
      into[2] = Math.max(into[2], Q[i].x + Q[i].r, lx)
      into[3] = Math.max(into[3], Q[i].y + Q[i].r)
    }
    return into
  }
  const bS = box(S, [Infinity, Infinity, -Infinity, -Infinity])
  const bNow = box(P, bS.slice())
  const pad = M + DRIFT
  const vbT = { x: bS[0] - pad, y: bS[1] - pad,
                w: (bS[2] - bS[0]) + pad * 2, h: (bS[3] - bS[1]) + pad * 2 }
  const vb = still ? Object.assign({}, vbT)
    : { x: bNow[0] - pad, y: bNow[1] - pad,
        w: (bNow[2] - bNow[0]) + pad * 2, h: (bNow[3] - bNow[1]) + pad * 2 }
  function paintBox() {
    svg.setAttribute("viewBox",
      [vb.x, vb.y, vb.w, vb.h].map(r2).join(" "))
  }
  paintBox()

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
    const g = el("g", { class: "jsm-item" + (P[i].fixed ? " jsm-pinned" : "") })
    const c = el("circle", {
      class: "jsm-node", "data-slug": nd.slug, cx: P[i].x, cy: P[i].y, r: P[i].r,
      // the settled coordinates: the layout of record, written once. cx/cy
      // drift from here every frame, these do not.
      "data-x0": S[i].x, "data-y0": S[i].y,
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
    return { g, c, badge, badgeText, label, enter: P[i].born < 1 }
  })

  // ── render: every frame, node parts and then the edges that hang off them ──
  function render() {
    const k = spread()
    for (let i = 0; i < n; i++) {
      const p = P[i]
      const x = p.x + offX(p), y = p.y + offY(p)
      p.rx = r2(k === 1 ? x : bx + (x - bx) * k)
      p.ry = r2(k === 1 ? y : by + (y - by) * k)
    }
    for (let i = 0; i < n; i++) {
      const p = P[i], q = parts[i]
      q.c.setAttribute("cx", p.rx); q.c.setAttribute("cy", p.ry)
      if (q.enter) {
        // a topic the filter just let back in grows and fades in rather
        // than blinking into existence
        q.c.setAttribute("r", r2(p.r * (0.55 + 0.45 * p.born)))
        q.g.style.opacity = String(r2(p.born))
        if (p.born >= 1) {
          q.enter = false
          q.c.setAttribute("r", p.r)
          q.g.style.opacity = ""
        }
      }
      if (q.badge) {
        q.badge.setAttribute("cx", r2(p.rx + p.r * 0.78))
        q.badge.setAttribute("cy", r2(p.ry - p.r * 0.78))
        q.badgeText.setAttribute("x", r2(p.rx + p.r * 0.78))
        q.badgeText.setAttribute("y", r2(p.ry - p.r * 0.78 + 3))
      }
      q.label.setAttribute("x", r2(p.right ? p.rx + p.r + 6 : p.rx - p.r - 6))
      q.label.setAttribute("y", r2(p.ry + 4))
    }
    for (const { ln, a, b } of lines) {
      ln.setAttribute("x1", P[a].rx); ln.setAttribute("y1", P[a].ry)
      ln.setAttribute("x2", P[b].rx); ln.setAttribute("y2", P[b].ry)
    }
  }

  // ── the frame loop ──
  // The same `integrate` the settle used, one tick per frame. A drag or a
  // filter change reheats alpha so the neighbourhood ripples instead of
  // snapping; left alone it falls back to the floor and the loop sleeps.
  let alpha = still ? 0 : 0.5
  let asleep = still
  let dragI = -1, dragMoved = false, dragWasFixed = false, focusI = -1
  let raf = 0, last = 0, saves = 0

  // hold this node's drift where it is, rather than at zero
  function freeze(i) {
    const p = P[i]
    if (!p.hold) { p.hx = wobX(p); p.hy = wobY(p) }
    p.hold = 1
  }

  function reheat(a) {
    if (still) return
    if (a > alpha) alpha = a
    asleep = false
  }

  function step(dt) {
    const s = Math.max(0.2, Math.min(2.5, dt / 0.01667))
    alpha = ALPHA_FLOOR + (alpha - ALPHA_FLOOR) * Math.pow(ALPHA_DECAY, s)
    return integrate(P, alpha, s)
  }

  function easeBox(dt) {
    const k = 1 - Math.exp(-dt / 0.3)
    let moved = false
    for (const key of ["x", "y", "w", "h"]) {
      const d = vbT[key] - vb[key]
      if (Math.abs(d) > 0.05) { vb[key] += d * k; moved = true }
      else if (vb[key] !== vbT[key]) { vb[key] = vbT[key]; moved = true }
    }
    if (moved) paintBox()
  }

  function save() {
    const out = {}
    for (let i = 0; i < n; i++) out[nodes[i].slug] = [r2(P[i].x), r2(P[i].y)]
    parentElement.__jsmPos = out
    parentElement.__jsmT = T
  }

  function frame(now) {
    raf = requestAnimationFrame(frame)
    let dt = (now - last) / 1000
    last = now
    if (!(dt > 0)) dt = 1 / 60
    if (dt > 0.08) dt = 0.08     // coming back from a hidden tab, not a lurch
    T += dt
    if (!asleep) {
      const hot = step(dt)
      // early exit: once alpha is on the floor and nothing is still moving,
      // the O(n^2) pass has nothing to say. The drift keeps going regardless.
      if (dragI < 0 && alpha <= ALPHA_FLOOR + 0.002 && hot < 0.02) asleep = true
    }
    for (let i = 0; i < n; i++) {
      if (P[i].born < 1) P[i].born = Math.min(1, P[i].born + dt / 0.42)
    }
    if (opening) {
      opening *= Math.exp(-dt / 0.38)
      if (opening < 0.002) opening = 0    // and then exactly full size
    }
    const thaw = Math.exp(-dt / 0.22)
    for (let i = 0; i < n; i++) {
      if (i === focusI || i === dragI) { P[i].hold = 1; continue }
      if (P[i].hold) { P[i].hold *= thaw; if (P[i].hold < 0.01) P[i].hold = 0 }
    }
    easeBox(dt)
    render()
    if ((++saves % 45) === 0) save()
  }

  function start() {
    if (still || raf) return
    last = performance.now()
    raf = requestAnimationFrame(frame)
  }
  function stop() {
    if (raf) cancelAnimationFrame(raf)
    raf = 0
  }
  const onVis = () => { if (document.hidden) { stop(); save() } else start() }

  render()
  if (!still) { if (!document.hidden) start() } else save()

  // ── hover: light the neighbourhood, dim the rest ──
  function setFocus(i) {
    if (i === focusI) return
    focusI = i
    for (const q of parts) q.g.classList.remove("jsm-lit", "jsm-hot")
    for (const ln of lines) ln.ln.classList.remove("jsm-lit")
    if (i < 0) { svg.classList.remove("jsm-focusing"); return }
    svg.classList.add("jsm-focusing")
    freeze(i)
    parts[i].g.classList.add("jsm-lit", "jsm-hot")
    for (const j of adj[i]) parts[j].g.classList.add("jsm-lit")
    for (const ln of lines) if (ln.a === i || ln.b === i) ln.ln.classList.add("jsm-lit")
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

  // ── drag to pin, click to open, double-click to let go ──
  function toLayout(ev) {
    const box2 = svg.getBoundingClientRect()
    const bv = svg.viewBox.baseVal
    if (!box2.width || !box2.height) return null
    // preserveAspectRatio is the default (meet, centred), so the drawing is
    // uniformly scaled and letterboxed inside the element
    const s = Math.min(box2.width / bv.width, box2.height / bv.height)
    const offX = (box2.width - bv.width * s) / 2
    const offY = (box2.height - bv.height * s) / 2
    return [bv.x + (ev.clientX - box2.left - offX) / s,
            bv.y + (ev.clientY - box2.top - offY) / s]
  }

  const livePins = {}
  for (const k in pins) livePins[k] = pins[k]
  let justDragged = false, navTimer = 0

  const hit = (ev) => (ev.target.closest && ev.target.closest("circle.jsm-node")) || null

  const onOver = (ev) => {
    const c = hit(ev)
    if (dragI >= 0) return
    if (!c) { hideTip(); setFocus(-1); return }
    const i = idx[c.dataset.slug]
    if (i === undefined) { hideTip(); setFocus(-1); return }
    setFocus(i)
    showTip(nodes[i], ev)
  }
  const onLeave = () => { hideTip(); setFocus(-1) }

  const onDown = (ev) => {
    const c = hit(ev)
    if (!c || ev.button !== 0) return
    const i = idx[c.dataset.slug]
    if (i === undefined) return
    ev.preventDefault()
    dragI = i
    freeze(i)
    dragMoved = false
    dragWasFixed = P[i].fixed
    P[i].fixed = true            // the hand is the constraint while it holds
    P[i].vx = 0; P[i].vy = 0
    reheat(0.55)
    hideTip()
  }
  const onMove = (ev) => {
    if (dragI < 0) return
    const pt = toLayout(ev)
    if (!pt) return
    dragMoved = true
    // subtract the drift so the circle sits under the cursor, not beside it
    P[dragI].x = r2(pt[0] - offX(P[dragI]))
    P[dragI].y = r2(pt[1] - offY(P[dragI]))
    reheat(0.5)
    if (still) render()
  }
  const onUp = () => {
    if (dragI < 0) return
    const i = dragI, moved = dragMoved
    dragI = -1
    if (!moved) { P[i].fixed = dragWasFixed; return }
    justDragged = true
    setTimeout(() => { justDragged = false }, 0)
    P[i].fixed = true
    parts[i].g.classList.add("jsm-pinned")
    livePins[nodes[i].slug] = [r2(P[i].x), r2(P[i].y)]
    reheat(0.45)
    setStateValue("pins", Object.assign({}, livePins))
  }
  const onDbl = (ev) => {
    const c = hit(ev)
    if (!c) return
    const i = idx[c.dataset.slug]
    if (i === undefined || !P[i].fixed) return
    if (navTimer) { clearTimeout(navTimer); navTimer = 0 }
    P[i].fixed = false
    parts[i].g.classList.remove("jsm-pinned")
    delete livePins[nodes[i].slug]
    reheat(0.5)
    setStateValue("pins", Object.assign({}, livePins))
  }
  const onClick = (ev) => {
    if (justDragged) return
    const c = hit(ev)
    if (!c) return
    const slug = c.dataset.slug
    const i = idx[slug]
    if (!slug || i === undefined) return
    const go = () => { window.location.assign("study?topic=" + encodeURIComponent(slug)) }
    // A pinned node also answers to a double-click, so its first click has
    // to wait long enough to find out whether a second one is coming.
    // Everything else opens the moment you let go.
    if (P[i].fixed) {
      if (navTimer) clearTimeout(navTimer)
      navTimer = setTimeout(() => { navTimer = 0; go() }, 220)
    } else go()
  }

  svg.addEventListener("mousemove", onOver)
  svg.addEventListener("mouseleave", onLeave)
  svg.addEventListener("mousedown", onDown)
  svg.addEventListener("click", onClick)
  svg.addEventListener("dblclick", onDbl)
  document.addEventListener("mousemove", onMove)
  document.addEventListener("mouseup", onUp)
  document.addEventListener("visibilitychange", onVis)

  return () => {
    stop()
    save()
    if (navTimer) clearTimeout(navTimer)
    svg.removeEventListener("mousemove", onOver)
    svg.removeEventListener("mouseleave", onLeave)
    svg.removeEventListener("mousedown", onDown)
    svg.removeEventListener("click", onClick)
    svg.removeEventListener("dblclick", onDbl)
    document.removeEventListener("mousemove", onMove)
    document.removeEventListener("mouseup", onUp)
    document.removeEventListener("visibilitychange", onVis)
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
