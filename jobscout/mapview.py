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

**Concept mode** draws the same map with the concept layer on it: a small
grey satellite per canonical concept, hung off the topic that names it, or
sitting between all of them when several do - which is the one thing a list
cannot show you. Prerequisites are drawn as arrows from what you read first
to what it unlocks.

That mode rests on one promise, and everything about how it is built is in
service of it: **the topics do not move.** So the layout runs in two passes.

* The hubs are settled from the topic-only node set, the topic-only seed and
  the `related`/`link` edges alone - the identical computation topic mode
  runs, on an array that has never heard of a satellite. A prerequisite is
  drawn but never pulls, because a spring on a prerequisite would slide every
  topic on the canvas the moment you switched modes.
* The satellites are then settled around the fixed hubs: pulled home toward
  the topic that names them, pushed off each other and off the hubs, one-way
  - a hub is never shoved by a satellite. Live, they are carried rather than
  simulated: a satellite's position is its hubs' position plus the offset the
  settle gave it, so a drag ripples out to the concepts and nothing can
  jostle a hub, and a hub's whole cluster breathes as one thing.

**What is packed is the names, not the dots.** That is the one thing the
first cut of this mode got wrong, and it failed on the real study base by a
mile: a concept is a six-pixel circle with a hundred-pixel name attached, so
separating the circles leaves the names lying across each other and no
amount of label-dodging afterwards can rescue a layout that never made room.
The satellite settle therefore separates *label boxes* - roughly 6.2 units a
character by twelve tall - and pushes an overlapping pair apart along
whichever axis they overlap least, which for a box that wide is nearly always
the vertical one, so a topic's concepts stack into a column you can read.

The rest follows from the element the drawing has to live in:

* The canvas is the height the drawing needs, up to 900px, and the viewBox is
  the box the drawing actually occupies - labels included - so neither axis
  is two bands of empty with a map between them. When the layout comes out
  taller than the element, the settle is turned toward the horizontal and run
  again, until the drawing is the shape of the box.
* The font is specified in layout units scaled by however far the drawing had
  to shrink, so a name is the same size to read at any density; a concept's
  name is a size down from a topic's, and a topic's carries the weight.
* Each name takes the first free seat out of right, left, above, below and a
  few label-heights up or down, measured against every other label and every
  circle, with repair sweeps afterwards because a greedy pass only ever knows
  what was free *so far*. A name that still has nowhere to sit is cut to
  eighteen characters, then to twelve - the concepts first, and hard, before
  a topic loses a character - and if it still has nowhere to sit, its dot is
  nudged clear and everybody is re-seated.

All of that is derived from the element's width, which a component does not
reliably know at mount: the width is asked of the first ancestor that has
one, and a `ResizeObserver` redoes the fit when a different one turns up.
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

# A concept is not a track and does not want to be mistaken for one: one
# neutral grey, one fixed size, so the eye reads "satellite" before it reads
# anything else about it.
CONCEPT_COLOR = "#7c8899"
CONCEPT_R = 6.0

# Radius carries `questions` - how many things this topic has actually asked
# you - because that is the depth signal, where `words` only ever measured
# how much the routine wrote that night. The real range across the study base
# is 2 to 10 questions, and that band is stretched across 10 to 22px so the
# difference is visible at a glance; sqrt inside the band so a topic with
# five times the questions is not five times as wide, and clamps so nothing
# vanishes or bullies the canvas.
Q_LO, Q_HI = 2.0, 10.0
RQ_LO, RQ_HI = 10.0, 22.0
R_MIN, R_MAX = 9.0, 22.0

LABEL_MAX = 28


def color_for(track: str | None) -> str:
    """The track's colour, or the grey every unlabelled topic shares."""
    return TRACK_COLORS.get((track or "").strip(), UNKNOWN_COLOR)


def radius_for_questions(questions) -> float:
    """Node radius from the topic's question count, sqrt-scaled and clamped.

    Anchored on the two ends of the range the study base actually spans:
    two questions is the smallest topic worth drawing at all and lands at
    `RQ_LO`, ten is the deepest and lands at `RQ_HI`. That is better than a
    2:1 spread across the real data, which is the point - a size signal you
    cannot see is not a signal.
    """
    try:
        q = max(0.0, float(questions))
    except (TypeError, ValueError):
        q = 0.0
    span = math.sqrt(Q_HI) - math.sqrt(Q_LO)
    t = (math.sqrt(q) - math.sqrt(Q_LO)) / span
    return round(min(R_MAX, max(R_MIN, RQ_LO + t * (RQ_HI - RQ_LO))), 2)


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


def is_concept(node) -> bool:
    """True for a concept satellite, whether it arrives as a node or an id."""
    if isinstance(node, dict):
        return node.get("kind") == "concept"
    return str(node or "").startswith("c:")


def with_concepts(graph: dict, result: dict) -> dict:
    """The topic graph with the concept layer laid over it.

    Three things are added and nothing is taken away: a `c:`-namespaced node
    per canonical concept carrying the slugs of every topic that names it, a
    thin `concept` edge from each of those topics to it, and a directed
    `prereq` edge per effective prerequisite, drawn from the prerequisite to
    the topic that depends on it - the direction you read in, not the
    direction the frontmatter is written in.

    `result` is a `jobscout.concepts.build`. A concept whose every topic is
    missing from this graph is dropped here rather than left for the filter,
    because a satellite with no hub has nowhere to sit.
    """
    nodes = [dict(n) for n in (graph.get("nodes") or [])]
    for n in nodes:
        n.setdefault("kind", "topic")
    known = {n["slug"] for n in nodes}
    edges = [dict(e) for e in (graph.get("edges") or [])]
    for _key, entry in sorted((result.get("concepts") or {}).items()):
        hubs = [s for s in entry["topics"] if s in known]
        if not hubs:
            continue
        nodes.append({
            "slug": entry["id"], "title": entry["canonical"], "kind": "concept",
            "track": None, "hubs": hubs, "tags": [], "words": 0,
            "questions": 0, "rounds": 0, "studied": False, "notes": 0,
        })
    edges.extend(dict(e) for e in (result.get("edges") or []))
    for slug, wanted in sorted((result.get("prereqs") or {}).items()):
        for p in wanted:
            edges.append({"source": p, "target": slug, "kind": "prereq"})
    return {"nodes": nodes, "edges": edges,
            "warnings": list(graph.get("warnings") or [])}


def filter_graph(graph: dict, *, tracks=None, unstudied_only: bool = False) -> dict:
    """The visible slice of the graph.

    `tracks=None` keeps every track; a list (even an empty one) keeps exactly
    what it names. An edge survives only when both of its ends did, so the
    picture never grows a line into nowhere.

    Concept satellites are not filtered on their own account - they have no
    track and nobody studies a concept. They are kept exactly as long as one
    of the topics that names them is still on the canvas, and dropped the
    moment the last one leaves.
    """
    nodes = [n for n in (graph.get("nodes") or []) if not is_concept(n)]
    sats = [n for n in (graph.get("nodes") or []) if is_concept(n)]
    if tracks is not None:
        keep = set(tracks)
        nodes = [n for n in nodes if n.get("track") in keep]
    if unstudied_only:
        nodes = [n for n in nodes if not n.get("studied")]
    visible = {n["slug"] for n in nodes}
    sats = [n for n in sats if visible.intersection(n.get("hubs") or ())]
    drawn = visible | {n["slug"] for n in sats}
    edges = [e for e in (graph.get("edges") or [])
             if e.get("source") in drawn and e.get("target") in drawn]
    return {"nodes": nodes + sats, "edges": edges}


def _clean_pins(pins, visible: set) -> dict:
    """Pins for topics that are actually on screen, as numbers.

    Whatever the browser last emitted comes back through session state, so a
    stale slug or a bad pair must not reach the layout. A concept is never
    pinnable - it belongs to its hub, not to the canvas - so a `c:` id here
    is a stale pin from some other mount and goes in the bin with the rest.
    """
    out = {}
    for slug, xy in (pins or {}).items():
        if slug not in visible or is_concept(slug):
            continue
        try:
            x, y = float(xy[0]), float(xy[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            out[slug] = [x, y]
    return out


def _edge_key(source: str, target: str, kind: str):
    """What makes two edges the same line.

    `related` and `link` are the same undirected thing said twice and have
    always collapsed to one line per pair. `prereq` and `concept` do not
    collapse into that or into each other: a pair can be related *and* carry
    a prerequisite arrow, and drawing only the first of those was the whole
    reason concept mode could not be layered on the old payload. Direction
    is part of the key for both, because both are arrows in fact even where
    only one is drawn as one.
    """
    if kind in ("prereq", "concept"):
        return (kind, source, target)
    return ("link",) + ((source, target) if source < target else (target, source))


def payload(graph: dict, pins=None) -> dict:
    """The `data` the component renders: nodes, edges, pins and the seed.

    Everything the drawing needs is computed here (colour, radius, label) so
    the JavaScript only lays out and paints, and so the choices are testable
    without a browser. Nodes and edges are sorted, which is what makes the
    seed and therefore the layout reproducible.

    The seed is taken from the *topic* slugs alone, never from the concept
    satellites. That is what lets the two modes agree: switching to concepts
    must not re-roll the ring the hubs start on, or every topic on the canvas
    would move and the mode would read as a different map rather than as the
    same map with more on it.
    """
    nodes = sorted(graph.get("nodes") or [], key=lambda n: n["slug"])
    visible = {n["slug"] for n in nodes}
    hubs = {n["slug"] for n in nodes if not is_concept(n)}
    out_nodes = []
    for n in nodes:
        title = n.get("title") or n["slug"]
        if is_concept(n):
            out_nodes.append({
                "slug": n["slug"],
                "title": title,
                "label": label_for(title),
                "kind": "concept",
                "track": None,
                "tags": [],
                "words": 0,
                "questions": 0,
                "studied": False,
                "notes": 0,
                "color": CONCEPT_COLOR,
                "r": CONCEPT_R,
                "hubs": sorted(s for s in (n.get("hubs") or []) if s in hubs),
            })
            continue
        track = n.get("track") or "unknown"
        out_nodes.append({
            "slug": n["slug"],
            "title": title,
            "label": label_for(title),
            "kind": "topic",
            "track": track,
            "tags": list(n.get("tags") or []),
            "words": int(n.get("words") or 0),
            "questions": int(n.get("questions") or 0),
            "studied": bool(n.get("studied")),
            "notes": int(n.get("notes") or 0),
            "color": color_for(track),
            "r": radius_for_questions(n.get("questions")),
        })
    seen = set()
    out_edges = []
    for e in graph.get("edges") or []:
        s, t = e.get("source"), e.get("target")
        if s not in visible or t not in visible or s == t:
            continue
        kind = e.get("kind", "link")
        key = _edge_key(s, t, kind)
        if key in seen:
            continue
        seen.add(key)
        out_edges.append({"source": s, "target": t, "kind": kind})
    out_edges.sort(key=lambda e: (e["source"], e["target"], e["kind"]))
    return {"nodes": out_nodes, "edges": out_edges,
            "pins": _clean_pins(pins, hubs), "seed": seed_for(hubs)}


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
/* a prerequisite is the one edge on this canvas that means something
   different in each direction, so it is the one edge that gets a head - and
   a little more weight, because it is also the only one worth following */
.jsm-edge.jsm-prereq {stroke: rgba(148,163,184,.62); stroke-width: 1.6;}
.jsm-arrowhead {fill: rgba(148,163,184,.62);}
/* topic to concept: present, not loud. These outnumber everything else five
   to one, and drawn at the weight of a related line they would be the only
   thing you could see. */
.jsm-edge.jsm-concept {stroke: rgba(148,163,184,.24); stroke-width: .8;}
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
/* a concept name is a caption on a topic, not a peer of one */
.jsm-label.jsm-label-c {fill-opacity: .62;}
/* and in concept mode a topic name is the thing you read first, so it keeps
   the weight the satellites do not get */
.jsm-label.jsm-label-h {font-weight: 620; fill-opacity: .95;}
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

  // ── two kinds of node ──
  // A hub is a topic. A satellite is a concept, and everything below that
  // reads "hub" means "the picture concept mode promises not to move".
  const isC = nodes.map((nd) => nd.kind === "concept")
  const HB = [], ST = []
  nodes.forEach((nd, i) => (isC[i] ? ST : HB).push(i))
  const nh = HB.length, ns = ST.length
  if (!nh) return                      // satellites with no hubs are nothing

  // The canvas grows with the crowd. 560px is right for the fourteen topics
  // the study base has; a hundred nodes in the same box is a smudge. That is
  // a budget rather than a height: the drawing is fitted into the box and
  // centred, so taking the whole 900px for a wide, short graph buys nothing
  // but two bands of empty. The final height is settled once the layout is
  // known, below.
  const HBUDGET = Math.round(Math.min(900, 560 + Math.max(0, n - 14) * 6))
  svg.style.height = HBUDGET + "px"

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
  const W = 900, VH = 520, CX = W / 2, CY = VH / 2
  const idx = {}
  nodes.forEach((nd, i) => { idx[nd.slug] = i })
  const hpos = {}                      // topic slug -> its seat in SH
  HB.forEach((gi, i) => { hpos[nodes[gi].slug] = i })

  // ── pass one: the hubs, and only the hubs ──
  // initial ring placement, jittered from the seed so symmetric graphs do
  // not sit in a force stalemate. Every hub draws, pinned or not, so the
  // sequence does not shift when you pin one. The count, the seed and the
  // order here are the topic set's alone: in topic mode there is nothing
  // else, and in concept mode the satellites have not been invented yet.
  // That is the whole trick behind "the hubs do not move between modes".
  const SH = HB.map((gi, i) => {
    const jitterA = rnd(), jitterR = rnd()
    const ang = TAU * (i + 0.35 * jitterA) / nh
    const rad = 45 + (0.62 * Math.min(W, VH) / 2) *
                Math.sqrt((i + 0.35 + 0.3 * jitterR) / nh)
    return { x: CX + rad * Math.cos(ang), y: CY + rad * Math.sin(ang),
             vx: 0, vy: 0, r: nodes[gi].r || 9, fixed: false }
  })
  for (const gi of HB) {
    const p = pins[nodes[gi].slug]
    const i = hpos[nodes[gi].slug]
    if (Array.isArray(p) && p.length === 2 && isFinite(p[0]) && isFinite(p[1])) {
      SH[i].x = +p[0]; SH[i].y = +p[1]; SH[i].fixed = true
    }
  }

  // Springs between hubs come from the `related`/`link` edges and from those
  // alone. A prerequisite is a fact about reading order, not a reason for
  // two topics to sit closer together, and letting it pull would move every
  // hub the instant you switched modes - which is the one thing this mode
  // promises not to do. It is drawn, with a head on it, and it pulls
  // nothing.
  const HL = []
  const seenHL = {}
  for (const e of edges) {
    if (e.kind === "prereq" || e.kind === "concept") continue
    const a = hpos[e.source], b = hpos[e.target]
    if (a === undefined || b === undefined || a === b) continue
    const k = a < b ? a + ":" + b : b + ":" + a
    if (seenHL[k]) continue
    seenHL[k] = 1
    HL.push([a, b])
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
  const hfx = new Float64Array(nh), hfy = new Float64Array(nh)

  // O(n^2). At the sizes a study base reaches (tens of topics, a hundred at
  // the outside) that is a few thousand pairs a frame, which is nothing; the
  // frame loop skips the whole pass once the graph is asleep anyway.
  function accumulate(Q, springs, fx, fy) {
    const m = Q.length
    fx.fill(0); fy.fill(0)
    for (let i = 0; i < m; i++) {
      for (let j = i + 1; j < m; j++) {
        let dx = Q[i].x - Q[j].x, dy = Q[i].y - Q[j].y
        let d2 = dx * dx + dy * dy
        if (d2 < 1e-6) { dx = 0.01 + i * 1e-4; dy = 0.013 + j * 1e-4; d2 = dx * dx + dy * dy }
        const d = Math.sqrt(d2), f = REP / d2
        const ux = (dx / d) * f, uy = (dy / d) * f
        fx[i] += ux; fy[i] += uy
        fx[j] -= ux; fy[j] -= uy
      }
    }
    for (const [a, b] of springs) {
      const dx = Q[b].x - Q[a].x, dy = Q[b].y - Q[a].y
      const d = Math.max(0.01, Math.sqrt(dx * dx + dy * dy))
      const f = (d - (Q[a].r + Q[b].r + GAP)) * SPRING
      const ux = (dx / d) * f, uy = (dy / d) * f
      fx[a] += ux; fy[a] += uy
      fx[b] -= ux; fy[b] -= uy
    }
    for (let i = 0; i < m; i++) {
      fx[i] += (CX - Q[i].x) * PULL
      fy[i] += (CY - Q[i].y) * PULL
    }
  }

  // positional collision. `k` is 1 for the settle, where overlap is a
  // guarantee, and softer in the frame loop, where a hard shove would read
  // as a twitch.
  function separate(Q, k) {
    const m = Q.length
    let moved = false
    for (let i = 0; i < m; i++) {
      for (let j = i + 1; j < m; j++) {
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
  function integrate(Q, springs, fx, fy, a, s) {
    accumulate(Q, springs, fx, fy)
    const decay = Math.pow(VDECAY, s)
    let hot = 0
    for (let i = 0; i < Q.length; i++) {
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
    accumulate(SH, HL, hfx, hfy)
    for (let i = 0; i < nh; i++) {
      if (SH[i].fixed) continue
      let dx = hfx[i] * alpha0, dy = hfy[i] * alpha0
      const m = Math.sqrt(dx * dx + dy * dy)
      if (m > MAXSTEP) { dx = (dx / m) * MAXSTEP; dy = (dy / m) * MAXSTEP }
      SH[i].x += dx; SH[i].y += dy
    }
    separate(SH, 1)
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
  for (let it = 0; it < 900; it++) if (integrate(SH, HL, hfx, hfy, 1, 1) < 0.004) break
  // the guarantee, not the tendency: circles must not overlap
  for (let k = 0; k < 400; k++) if (!separate(SH, 1)) break
  const r2 = (v) => Math.round(v * 100) / 100
  for (let i = 0; i < nh; i++) {
    SH[i].x = r2(SH[i].x); SH[i].y = r2(SH[i].y); SH[i].vx = 0; SH[i].vy = 0
  }

  // ── pass two: the satellites ──
  // The hubs are finished and will not move again. What is left is to find
  // every concept a seat near the topic that names it - and this is where
  // the first cut of concept mode went wrong: it reserved the room a
  // satellite's six-pixel *circle* needs. A satellite is a dot with a
  // hundred-pixel name attached, so the thing being packed is the label
  // box, and that is what the separation pass below separates.
  const S = new Array(n)
  HB.forEach((gi, i) => { S[gi] = SH[i] })
  const satRef = {}                    // satellite -> hubs it hangs off

  // Concept mode is drawn at the same scale as topic mode: K = 1. A uniform
  // blow-up of the hub layout was the obvious way to make room and it buys
  // exactly nothing, because the viewBox is fitted to the drawing and the
  // font is then scaled by however far the drawing had to shrink - scale the
  // layout by K and the fit divides it straight back out, leaving every
  // name the same size against the same gap. What is actually scarce is the
  // element: elW by 900 pixels of screen, and the only way to fit a hundred
  // and eighteen names into it is to pack them by their boxes, use the whole
  // height, and shorten the ones that still will not sit down.
  const anchors = {}                   // satellite -> where it hangs
  const groupN = {}                    // hub set -> how many hang there
  for (const gi of ST) {
    const hs = (nodes[gi].hubs || []).filter((s) => hpos[s] !== undefined)
    const seats = hs.length ? hs.map((s) => hpos[s]) : [0]
    let ax = 0, ay = 0, r = 0
    for (const h of seats) { ax += SH[h].x; ay += SH[h].y; r = Math.max(r, SH[h].r) }
    const key = seats.join(",")
    groupN[key] = (groupN[key] || 0) + 1
    anchors[gi] = { seats, key, r: seats.length > 1 ? 6 : r,
                    ax: ax / seats.length, ay: ay / seats.length }
  }

  // ── what a name takes up ──
  // 6.2 units a character by 12 tall, which is close enough at this font not
  // to be worth measuring. A concept's name is drawn a size down from a
  // topic's - it is a caption on a topic, not a peer of one - and `FS`
  // scales the lot by however far the drawing had to shrink, so a name is
  // the same size to read at any density.
  const CH = 6.2, LPAD = 10, LH = 12, SATF = 0.9, CUT = 18
  const MIN_LABEL_PX = 11, M = 26, DRIFT = 6
  const fullText = nodes.map((nd) => nd.label || nd.slug)
  const text = fullText.slice()
  const lw = new Float64Array(n), lh = new Float64Array(n)
  const lx = new Float64Array(n), ly = new Float64Array(n)
  const anc = new Uint8Array(n)        // 0 start, 1 end, 2 middle
  const side = new Uint8Array(n)       // which way the reserved box points
  let FS = 1, elH = HBUDGET, elW = 700
  // Which way a jammed pair is pushed apart shapes the whole drawing: all
  // vertical and it grows into a tall ribbon with the canvas empty either
  // side of it, all horizontal and it grows into a band. VB tilts that
  // choice, and the fit below turns it until the drawing is the shape of
  // the element it has to live in.
  let VB = 1
  const fsOf = (i) => FS * (isC[i] ? SATF : 1)
  const cut = (t, k) =>
    t.length <= k ? t : t.slice(0, k - 1).replace(/\\s+$/, "") + "\\u2026"
  function metrics() {
    for (let i = 0; i < n; i++) {
      lw[i] = (text[i].length * CH + LPAD) * fsOf(i)
      lh[i] = LH * fsOf(i)
    }
  }

  // The element's width, asked of the first ancestor that has one. A
  // component can mount before the browser has laid its column out, and the
  // old code's `|| 700` fallback then sized the whole drawing for a canvas
  // 15% narrower than the one it landed in - which is how a map ends up
  // letterboxed into two bands of empty. `relayout` below is the other half
  // of the answer: when a real width does arrive, the fit is simply redone.
  function measureW() {
    let e = svg
    for (let hop = 0; e && hop < 5; hop++) {
      const w = e.getBoundingClientRect().width
      if (w > 1) return w
      e = e.parentElement
    }
    return 700
  }

  // ── the satellite settle ──
  // Satellites repel each other and the hubs, and are pulled back toward the
  // topic that names them (or toward the centre of gravity of all of them,
  // when several do - which is the one thing this mode exists to show).
  // Hubs are fixed: a satellite is never allowed to shove one, which is what
  // carries the hub layout through the second pass intact.
  //
  // Two boxes that overlap are pushed apart along whichever axis they
  // overlap *least*. For a box a hundred units wide and fifteen tall that is
  // nearly always the vertical one, so a hub's concepts stack into a column
  // you can read down instead of a smear you cannot read at all.
  const hyE = new Float64Array(n), hlE = new Float64Array(n), hrE = new Float64Array(n)
  function extents() {
    for (let i = 0; i < n; i++) {
      const r = S[i].r
      const w = r + 7 + lw[i]
      if (isC[i]) {
        hyE[i] = Math.max(r, lh[i] * 0.62) + lh[i] * 0.6
        if (side[i]) { hlE[i] = r + 2; hrE[i] = w } else { hlE[i] = w; hrE[i] = r + 2 }
      } else {
        // a hub reserves both sides and a line's clearance above and below.
        // There are fourteen of them against a hundred satellites, so the
        // room is cheap - and a topic's name is the one the reader steers by,
        // so it is the one that must not have to go looking for a seat.
        hyE[i] = Math.max(r, lh[i] * 0.62) + lh[i] * 1.35
        hlE[i] = w; hrE[i] = w
      }
    }
  }
  const sweepOrd = new Array(n)
  function settleSats() {
    if (!ns) return
    const used = {}
    for (const gi of ST) {
      const a = anchors[gi], many = groupN[a.key]
      const k = used[a.key] = (used[a.key] || 0) + 1
      // the first seat is hashed from the hub set, so a topic's concepts do
      // not all start at three o'clock and lean the same way
      const a0 = (hash32(a.key) % 997) / 997 * TAU
      const ang = a0 + TAU * (k - 0.5) / many
      const rad = a.r + 16 + many * 2
      S[gi] = { x: a.ax + rad * Math.cos(ang), y: a.ay + rad * 0.7 * Math.sin(ang),
                vx: 0, vy: 0, r: nodes[gi].r || 6, fixed: false, seats: a.seats }
    }
    let mid = 0
    for (let i = 0; i < n; i++) mid += S[i].x
    mid /= n
    for (const gi of ST) side[gi] = S[gi].x >= anchors[gi].ax ? 1 : 0
    for (const gi of HB) side[gi] = S[gi].x >= mid ? 1 : 0
    for (let i = 0; i < n; i++) sweepOrd[i] = i
    for (let it = 0; it < 420; it++) {
      // home again: a concept that has been pushed away drifts back toward
      // the topic it belongs to, so the clusters stay legible as clusters
      for (const gi of ST) {
        const a = anchors[gi], p = S[gi]
        const dx = a.ax - p.x, dy = a.ay - p.y
        const d = Math.hypot(dx, dy) || 1
        const rest = a.r + 14
        if (d > rest) {
          const s = Math.min(1.4, (d - rest) * 0.18)
          p.x += (dx / d) * s; p.y += (dy / d) * s
        }
      }
      if ((it % 24) === 0) {
        for (const gi of ST) side[gi] = S[gi].x >= anchors[gi].ax ? 1 : 0
      }
      extents()
      let maxHY = 0
      for (let i = 0; i < n; i++) if (hyE[i] > maxHY) maxHY = hyE[i]
      // a sweep down the y axis: two boxes fifteen units tall a hundred
      // apart cannot touch, and skipping those pairs is what keeps an
      // O(n^2) settle cheap enough to run four times before the first paint
      sweepOrd.sort((a, b) => S[a].y - S[b].y || a - b)
      for (let ii = 0; ii < n; ii++) {
        const i = sweepOrd[ii]
        for (let jj = ii + 1; jj < n; jj++) {
          const j = sweepOrd[jj]
          const dy = S[j].y - S[i].y
          if (dy > hyE[i] + maxHY) break
          const wi = isC[i] ? 1 : 0, wj = isC[j] ? 1 : 0
          if (!wi && !wj) continue          // two hubs are not our business
          const oy = hyE[i] + hyE[j] - Math.abs(dy)
          if (oy <= 0) continue
          const ox = Math.min(S[i].x + hrE[i], S[j].x + hrE[j]) -
                     Math.max(S[i].x - hlE[i], S[j].x - hlE[j])
          if (ox <= 0) continue
          const tot = wi + wj
          if (oy * VB <= ox) {
            const s = (oy + 0.4) / tot, dir = dy >= 0 ? 1 : -1
            if (wi) S[i].y -= dir * s
            if (wj) S[j].y += dir * s
          } else {
            const s = (ox + 0.4) / tot, dir = S[j].x >= S[i].x ? 1 : -1
            if (wi) S[i].x -= dir * s
            if (wj) S[j].x += dir * s
          }
        }
      }
    }
    for (const gi of ST) { S[gi].x = r2(S[gi].x); S[gi].y = r2(S[gi].y) }
  }

  // ── seating the labels ──
  // Each name takes the first free seat out of "preferred side, other side,
  // above, below, and a few label-heights up or down", measured against the
  // labels already seated and against every circle on the canvas. A name
  // that is still sitting on something after all of that is shortened -
  // eighteen characters and an ellipsis - and the whole pass is run again,
  // because a shorter name frees a seat for its neighbours too.
  // The seat, plus the room the drift needs. Nothing on this canvas is ever
  // quite still, so two names seated a hair apart will breathe into each
  // other; DPAD is the couple of pixels of wander that buys back.
  const DPAD = 2.4
  function seatBox(i, k, dy) {
    const r = S[i].r, w = lw[i], h = lh[i], base = 4 * fsOf(i)
    let x0, yb
    if (k === 0) { x0 = S[i].x + r + 6; yb = S[i].y + base + dy }
    else if (k === 1) { x0 = S[i].x - r - 6 - w; yb = S[i].y + base + dy }
    else if (k === 2) { x0 = S[i].x - w / 2; yb = S[i].y - r - 6 }
    else { x0 = S[i].x - w / 2; yb = S[i].y + r + 6 + h * 0.82 }
    return [x0 - DPAD, yb - h * 0.82 - DPAD, x0 + w + DPAD, yb + h * 0.24 + DPAD, yb]
  }
  function ov(a, b) {
    const ox = Math.min(a[2], b[2]) - Math.max(a[0], b[0])
    const oy = Math.min(a[3], b[3]) - Math.max(a[1], b[1])
    return (ox > 0 && oy > 0) ? Math.min(ox, oy) : 0
  }
  // circles are still obstacles; a name across a node is as unreadable as a
  // name across another name
  function hitsCircle(b, i) {
    let pen = 0
    for (let j = 0; j < n; j++) {
      if (j === i) continue
      const ox = Math.min(b[2], S[j].x + S[j].r) - Math.max(b[0], S[j].x - S[j].r)
      if (ox <= 0) continue
      const oy = Math.min(b[3], S[j].y + S[j].r) - Math.max(b[1], S[j].y - S[j].r)
      if (oy > 0) pen += Math.min(ox, oy)
    }
    return pen
  }
  const boxes = new Array(n)           // where each name ended up sitting
  function seat() {
    for (let i = 0; i < n; i++) text[i] = fullText[i]
    let mid = 0
    for (let i = 0; i < n; i++) mid += S[i].x
    mid /= n
    for (let pass = 0; pass < 5; pass++) {
      metrics()
      // hubs are seated first and satellites dodge them: a topic's name is
      // the one the reader is navigating by, and it has nowhere else to go
      const ord = Array.from({ length: n }, (_, i) => i)
        .sort((a, b) => (isC[a] ? 1 : 0) - (isC[b] ? 1 : 0) ||
                        (S[a].y - S[b].y) || (S[a].x - S[b].x) || (a - b))
      // one candidate seat is a side (right, left, above, below) and, for the
      // two sides, an offset of so many label-heights up or down
      function seats(i) {
        const pref = S[i].x >= mid ? 0 : 1, other = 1 - pref
        const H = lh[i] * 1.2
        const out = [[pref, 0], [other, 0], [2, 0], [3, 0]]
        for (let s = 1; s <= 6; s++) {
          out.push([pref, s * H], [pref, -s * H], [other, s * H], [other, -s * H])
        }
        out.pref = pref
        return out
      }
      // what this seat would sit on: the labels already placed, plus every
      // circle on the canvas. `skip` is the label's own node on a re-seat,
      // where every other label is already down.
      function penalty(i, b, placed, skip) {
        let pen = 0
        for (const j of placed) {
          const q = boxes[j]
          if (j === skip || !q || q[1] > b[3] || q[3] < b[1]) continue
          pen += ov(b, q)
        }
        return pen + hitsCircle(b, i) * 0.7
      }
      function place(i, placed, skip) {
        const cands = seats(i), pref = cands.pref
        let best = null, bestCost = Infinity, bestPen = Infinity
        for (const [k, dy] of cands) {
          const b = seatBox(i, k, dy)
          const pen = penalty(i, b, placed, skip)
          const cost = pen * 10 + Math.abs(dy) * 0.05 +
                       (k === pref ? 0 : k === 1 - pref ? 0.4 : 0.9)
          if (cost < bestCost) { bestCost = cost; bestPen = pen; best = [b, k, dy] }
          if (pen === 0 && k === pref && dy === 0) break
        }
        const [b, k] = best
        anc[i] = k === 0 ? 0 : k === 1 ? 1 : 2
        lx[i] = k === 0 ? S[i].r + 6 : k === 1 ? -(S[i].r + 6) : 0
        ly[i] = b[4] - S[i].y
        boxes[i] = b
        return bestPen
      }
      const placed = []
      for (const i of ord) { place(i, placed, -1); placed.push(i) }
      // A greedy pass answers "what is free *so far*", which is the wrong
      // question for everyone seated early. Repair sweeps re-ask it with the
      // whole picture down, which is what clears the last few - and the
      // verdict is taken afterwards, against the seats as they finally are,
      // because a sweep can seat someone onto a name it has already passed.
      let bad = []
      for (let rep = 0; rep < 5; rep++) {
        let hit = 0
        for (const i of ord) if (place(i, ord, i) > 0.4) hit++
        bad = []
        for (const i of ord) if (penalty(i, boxes[i], ord, i) > 0.4) bad.push(i)
        if (!bad.length || !hit) break
      }
      if (!bad.length) return
      // An ellipsis is worth more than a name you cannot read because
      // another name is lying across it - but it is spent on the concepts
      // first, and hard on the concepts, before a topic loses a character.
      // The topics are what the reader is steering by.
      const limit = (pass === 0 || pass === 2) ? CUT : 12
      const hubsToo = pass >= 2
      let changed = false
      for (const i of bad) {
        if ((!hubsToo && !isC[i]) || text[i].length <= limit) continue
        text[i] = cut(fullText[i], limit); changed = true
      }
      if (!changed) return
    }
  }

  // A name that *still* has nowhere to sit does not need a shorter name, it
  // needs its dot moved. A satellite whose label is trapped is nudged clear
  // along the axis it is trapped on and everybody is re-seated - which is
  // the one move a fixed layout cannot make for itself, and it is what
  // clears the last one or two pairs on a really crowded base.
  function polish() {
    if (!ns) return
    for (let round = 0; round < 5; round++) {
      let moved = false
      for (const gi of ST) {
        const b = boxes[gi]
        if (!b) continue
        let step = 0, worst = 0
        for (let j = 0; j < n; j++) {
          const q = boxes[j]
          if (j === gi || !q) continue
          const ox = Math.min(b[2], q[2]) - Math.max(b[0], q[0])
          if (ox <= 0) continue
          const oy = Math.min(b[3], q[3]) - Math.max(b[1], q[1])
          if (oy <= 0) continue
          const d = Math.min(ox, oy)
          if (d > worst) {
            worst = d
            step = (b[1] + b[3] >= q[1] + q[3] ? 1 : -1) * (oy + 2)
          }
        }
        if (worst > 0.4) { S[gi].y += step; moved = true }
      }
      if (!moved) return
      seat()
    }
  }

  // ── the frame ──
  // The box the drawing needs, labels included, and the element it is fitted
  // into.
  function boxOf(Q, into) {
    for (let i = 0; i < n; i++) {
      const x = Q[i].x, y = Q[i].y, r = Q[i].r
      const x0 = x + lx[i] - (anc[i] === 1 ? lw[i] : anc[i] === 2 ? lw[i] / 2 : 0)
      const yb = y + ly[i]
      into[0] = Math.min(into[0], x - r, x0)
      into[1] = Math.min(into[1], y - r, yb - lh[i] * 0.82)
      into[2] = Math.max(into[2], x + r, x0 + lw[i])
      into[3] = Math.max(into[3], y + r, yb + lh[i] * 0.24)
    }
    return into
  }
  // A crowded concept map is allowed a shorter canvas than the 560 a small
  // map has always had: a wide, short drawing pinned to 560 is letterboxed,
  // and two bands of empty with a map between them is the thing this pass
  // exists to stop. A handful of nodes keeps the old floor.
  const MINH = (ns && n > 30) ? 340 : 560
  const fit = (bw, bh) =>
    Math.max(MINH, Math.min(HBUDGET, Math.round(elW * bh / bw)))

  // FS depends on the frame and the frame depends on FS (a bigger name is a
  // wider drawing, and in concept mode a wider drawing is also a differently
  // settled one). Four damped rounds land well inside a pixel; the clamps
  // are there so a pathological graph cannot chase its own tail.
  function layout() {
    elW = measureW()
    VB = 1
    for (let round = 0; round < 7; round++) {
      metrics()
      settleSats()
      seat()
      const b = boxOf(S, [Infinity, Infinity, -Infinity, -Infinity])
      const bw = (b[2] - b[0]) + 2 * (M + DRIFT)
      const bh = (b[3] - b[1]) + 2 * (M + DRIFT)
      elH = fit(bw, bh)
      const sc = Math.min(elW / bw, elH / bh)
      const want = Math.min(3.6, Math.max(1, MIN_LABEL_PX / (11 * sc)))
      // the shape the element wants against the shape the drawing came out;
      // a drawing taller than its box is one that should have spread sideways
      const tall = (bh / bw) / (HBUDGET / elW)
      const vb = Math.min(6, Math.max(0.3, VB * Math.pow(tall, 0.7)))
      const done = Math.abs(want - FS) < 0.02 &&
                   (!ns || Math.abs(vb - VB) < 0.04)
      FS += (want - FS) * 0.85
      VB += (vb - VB) * 0.7
      if (done) break
    }
    metrics()
    seat()
    polish()
    for (const gi of ST) {
      const p = S[gi]
      let cx = 0, cy = 0
      for (const h of p.seats) { cx += SH[h].x; cy += SH[h].y }
      // what a satellite remembers is its offset from its hubs, so a dragged
      // topic takes its concepts with it instead of leaving them behind
      satRef[gi] = { h: p.seats, dx: p.x - cx / p.seats.length,
                     dy: p.y - cy / p.seats.length }
    }
    const b = boxOf(S, [Infinity, Infinity, -Infinity, -Infinity])
    const pad = M + DRIFT
    svg.style.height =
      fit((b[2] - b[0]) + pad * 2, (b[3] - b[1]) + pad * 2) + "px"
    return { x: b[0] - pad, y: b[1] - pad,
             w: (b[2] - b[0]) + pad * 2, h: (b[3] - b[1]) + pad * 2 }
  }
  const vbT = layout()

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
      born: warm ? 1 : 0, hold: 0, hx: 0, hy: 0,
      // a concept does not breathe on its own account: it is carried by the
      // topic that names it, so a hub's whole cluster wanders as one thing
      // and two names in it can never crawl across each other
      ax: isC[i] ? 0 : 2.0 + 1.6 * w(), ay: isC[i] ? 0 : 1.8 + 1.6 * w(),
      f1: 0.52 + 0.46 * w(), f2: 0.19 + 0.22 * w(),
      f3: 0.49 + 0.46 * w(), f4: 0.17 + 0.22 * w(),
      p1: TAU * w(), p2: TAU * w(), p3: TAU * w(), p4: TAU * w(),
    }
  })
  const PH = HB.map((gi) => P[gi])
  // Satellites are not simulated live: they are carried. Their live position
  // is their hubs' live position plus the offset the settle gave them, so a
  // drag ripples out to the concepts without a second physics loop and
  // without anything on the canvas being able to jostle a hub.
  function followSats() {
    for (const gi of ST) {
      const ref = satRef[gi]
      let cx = 0, cy = 0
      for (const h of ref.h) {
        cx += PH[h].x + offX(PH[h]); cy += PH[h].y + offY(PH[h])
      }
      P[gi].x = cx / ref.h.length + ref.dx
      P[gi].y = cy / ref.h.length + ref.dy
    }
  }
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
  followSats()                         // wants offX/offY: a cluster drifts as one

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

  // The frame is the settled layout plus room for the drift. When nodes are
  // sliding in from an older, wider picture the box starts around both and
  // eases down to the settled one, so nothing is ever clipped mid-move.
  const pad = M + DRIFT
  const bNow = boxOf(P, [vbT.x + pad, vbT.y + pad,
                         vbT.x + vbT.w - pad, vbT.y + vbT.h - pad])
  const vb = still ? Object.assign({}, vbT)
    : { x: bNow[0] - pad, y: bNow[1] - pad,
        w: (bNow[2] - bNow[0]) + pad * 2, h: (bNow[3] - bNow[1]) + pad * 2 }
  function paintBox() {
    svg.setAttribute("viewBox",
      [vb.x, vb.y, vb.w, vb.h].map(r2).join(" "))
  }
  paintBox()

  // the arrowhead. One marker for the whole canvas, its id counted up per
  // mount so two maps on one page cannot borrow each other's.
  window.__jsmArrow = (window.__jsmArrow || 0) + 1
  const MID = "jsm-arrow-" + window.__jsmArrow
  const defs = el("defs", {})
  const marker = el("marker", {
    id: MID, viewBox: "0 0 10 10", refX: "9.5", refY: "5",
    markerWidth: "5.5", markerHeight: "5.5", orient: "auto-start-reverse",
  })
  marker.appendChild(el("path", { class: "jsm-arrowhead", d: "M 0 1 L 10 5 L 0 9 z" }))
  defs.appendChild(marker)
  svg.appendChild(defs)

  const gEdges = el("g", { class: "jsm-edges" })
  const gNodes = el("g", { class: "jsm-nodes" })
  svg.appendChild(gEdges)
  svg.appendChild(gNodes)

  const adj = nodes.map(() => [])
  const lines = []
  for (const e of edges) {
    const a = idx[e.source], b = idx[e.target]
    if (a === undefined || b === undefined || a === b) continue
    adj[a].push(b); adj[b].push(a)
    const arrow = e.kind === "prereq"
    const cls = "jsm-edge" +
      (e.kind === "related" ? " jsm-related" : "") +
      (e.kind === "concept" ? " jsm-concept" : "") +
      (arrow ? " jsm-prereq" : "")
    const ln = el("line", {
      class: cls, "data-source": e.source, "data-target": e.target,
      "data-kind": e.kind || "link",
      x1: P[a].x, y1: P[a].y, x2: P[b].x, y2: P[b].y,
    })
    if (arrow) ln.setAttribute("marker-end", "url(#" + MID + ")")
    gEdges.appendChild(ln)
    lines.push({ ln, a, b, arrow })
  }

  const parts = nodes.map((nd, i) => {
    const g = el("g", { class: "jsm-item" + (P[i].fixed ? " jsm-pinned" : "") })
    const c = el("circle", {
      class: "jsm-node", "data-slug": nd.slug, "data-kind": nd.kind || "topic",
      cx: P[i].x, cy: P[i].y, r: P[i].r,
      // the settled coordinates: the layout of record, written once. cx/cy
      // drift from here every frame, these do not.
      "data-x0": S[i].x, "data-y0": S[i].y,
      fill: nd.color, "fill-opacity": isC[i] ? "0.6" : "0.85", stroke: nd.color,
      "stroke-width": nd.studied ? "2" : "1.5",
      "stroke-opacity": nd.studied ? "1" : "0.55",
    })
    if (!nd.studied && !isC[i]) c.setAttribute("stroke-dasharray", "3 3")
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

    // a hub keeps the heavier label in concept mode: with five satellites to
    // every topic, the topics have to stay the thing you read first
    const label = el("text", {
      class: "jsm-label" + (isC[i] ? " jsm-label-c" : ns ? " jsm-label-h" : ""),
    })
    g.appendChild(label)
    gNodes.appendChild(g)
    return { g, c, badge, badgeText, label, enter: P[i].born < 1 }
  })

  // The size and the seat are re-derived whenever the element changes width,
  // so this is a function rather than four attributes written once.
  function dressLabels() {
    for (let i = 0; i < n; i++) {
      const label = parts[i].label
      // an inline style, not the `font-size` attribute: the stylesheet's own
      // rule would win over a presentation attribute and undo the scaling
      const f = fsOf(i)
      label.style.fontSize = r2(11 * f) + "px"
      label.style.strokeWidth = r2(3 * f) + "px"
      label.setAttribute("text-anchor",
        anc[i] === 0 ? "start" : anc[i] === 1 ? "end" : "middle")
      if (label.textContent !== text[i]) label.textContent = text[i]
    }
  }

  // ── render: every frame, node parts and then the edges that hang off them ──
  function render() {
    const k = spread()
    if (ns) followSats()
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
      q.label.setAttribute("x", r2(p.rx + lx[i]))
      q.label.setAttribute("y", r2(p.ry + ly[i]))
    }
    for (const { ln, a, b, arrow } of lines) {
      let x1 = P[a].rx, y1 = P[a].ry, x2 = P[b].rx, y2 = P[b].ry
      if (arrow) {
        // stop the line at the rim of each circle, or the head would be
        // buried under the topic it points at
        const dx = x2 - x1, dy = y2 - y1
        const d = Math.hypot(dx, dy) || 1
        x1 += (dx / d) * (P[a].r + 2); y1 += (dy / d) * (P[a].r + 2)
        x2 -= (dx / d) * (P[b].r + 6); y2 -= (dy / d) * (P[b].r + 6)
        // and shift it off the related line the same pair may already have,
        // so "these two go together" and "this one comes first" are two
        // things you can see at once rather than one line drawn twice
        const nx = -(dy / d) * 5, ny = (dx / d) * 5
        x1 = r2(x1 + nx); y1 = r2(y1 + ny); x2 = r2(x2 + nx); y2 = r2(y2 + ny)
      }
      ln.setAttribute("x1", x1); ln.setAttribute("y1", y1)
      ln.setAttribute("x2", x2); ln.setAttribute("y2", y2)
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
    return integrate(PH, HL, hfx, hfy, alpha, s)
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

  // ── the element changed width ──
  // The whole fit - the font scale, where every name sits, how tall the
  // canvas is and what the viewBox frames - is derived from the width of the
  // element. That width is not known at mount if the browser has not laid
  // the column out yet, and it changes again whenever the window does, so
  // the answer is to be able to redo it rather than to guess once.
  let lastW = elW
  function relayout() {
    const w = measureW()
    if (!(w > 1) || Math.abs(w - lastW) / lastW < 0.02) return
    lastW = w
    const box = layout()
    for (const k in box) { vbT[k] = box[k]; if (still) vb[k] = box[k] }
    dressLabels()
    followSats()
    paintBox()
    render()
    reheat(0.2)
  }
  const ro = (typeof ResizeObserver === "function")
    ? new ResizeObserver(relayout) : null
  if (ro) ro.observe(svg)

  dressLabels()
  render()
  if (!still) { if (!document.hidden) start() } else save()

  // ── hover: light the neighbourhood, dim the rest ──
  // In concept mode this is the reading: hover a concept and every topic
  // that names it lights up, which is the question "who else teaches this"
  // answered without leaving the page. Hover a topic and you get its
  // concepts and its prerequisites, because both hang off it as edges.
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
    let bits
    if (nd.kind === "concept") {
      const many = (nd.hubs || []).length
      bits = ["concept", many === 1 ? "1 topic" : many + " topics"]
    } else {
      bits = [nd.track, nd.studied ? "studied" : "not studied yet"]
      if (nd.questions) {
        bits.push(nd.questions + (nd.questions === 1 ? " question" : " questions"))
      }
      if (nd.notes) bits.push(nd.notes + (nd.notes === 1 ? " note" : " notes"))
    }
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

  // A concept belongs to its hub, not to the canvas: it cannot be dragged
  // and it cannot be pinned, because a pinned satellite would be a concept
  // that had stopped meaning "this topic teaches this".
  const onDown = (ev) => {
    const c = hit(ev)
    if (!c || ev.button !== 0) return
    const i = idx[c.dataset.slug]
    if (i === undefined || isC[i]) return
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
    if (i === undefined || isC[i] || !P[i].fixed) return
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
    // a topic opens its page; a concept opens the sheet at its own line,
    // which is the same link the chips on the topic page use
    const go = () => {
      window.location.assign(isC[i]
        ? "concepts?c=" + encodeURIComponent(slug)
        : "study?topic=" + encodeURIComponent(slug))
    }
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
    if (ro) ro.disconnect()
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
