"""The pure-Python half of the study map: what gets drawn, how big, what
colour, and what the browser is handed. The layout itself lives in the
component's JavaScript and is covered by the e2e tests.
"""
from jobscout import mapview


def g(nodes, edges=()):
    return {"nodes": list(nodes), "edges": list(edges), "warnings": []}


def node(slug, **kw):
    base = {"slug": slug, "title": slug.replace("-", " ").title(),
            "track": "backend", "tags": [], "words": 400, "questions": 6,
            "studied": False, "notes": 0}
    base.update(kw)
    return base


def concept(cid, canonical, hubs):
    return {"slug": cid, "title": canonical, "kind": "concept", "track": None,
            "hubs": list(hubs), "tags": [], "words": 0, "questions": 0,
            "studied": False, "notes": 0}


# ── colours ─────────────────────────────────────────────────────────

def test_every_track_has_its_own_colour():
    tracks = ["dsa", "backend", "system-design", "llm-infra", "resume"]
    colors = [mapview.color_for(t) for t in tracks]
    assert len(set(colors)) == len(tracks)
    assert all(c.startswith("#") and len(c) == 7 for c in colors)


def test_unknown_and_missing_tracks_fall_back_to_grey():
    assert mapview.color_for("unknown") == mapview.UNKNOWN_COLOR
    assert mapview.color_for(None) == mapview.UNKNOWN_COLOR
    assert mapview.color_for("") == mapview.UNKNOWN_COLOR
    assert mapview.color_for("not-a-track") == mapview.UNKNOWN_COLOR


# ── radius ──────────────────────────────────────────────────────────

def test_radius_is_clamped_and_monotonic_in_questions():
    assert mapview.radius_for_questions(0) == mapview.R_MIN
    assert mapview.radius_for_questions(1) == mapview.R_MIN
    assert mapview.radius_for_questions(10_000) == mapview.R_MAX
    small, mid, big = (mapview.radius_for_questions(q) for q in (3, 6, 9))
    assert mapview.R_MIN <= small < mid < big <= mapview.R_MAX


def test_radius_spreads_the_real_question_range_at_least_two_to_one():
    """The whole reason size moved off `words`: a signal you cannot see is
    not a signal. Across the range the study base actually spans - 2 to 10
    questions - the deepest topic must draw at least twice the radius of the
    shallowest, and the ends must land where the constants say they do."""
    lo = mapview.radius_for_questions(mapview.Q_LO)
    hi = mapview.radius_for_questions(mapview.Q_HI)
    assert lo == mapview.RQ_LO and hi == mapview.RQ_HI
    assert hi / lo >= 2.0, f"{hi} / {lo} is not a 2:1 spread"
    # and it is a curve, not a ramp: the first extra question is worth more
    # than the last one
    assert (mapview.radius_for_questions(4) - mapview.radius_for_questions(3) >
            mapview.radius_for_questions(10) - mapview.radius_for_questions(9))


def test_radius_survives_junk_question_counts():
    assert mapview.radius_for_questions(None) == mapview.R_MIN
    assert mapview.radius_for_questions("not a number") == mapview.R_MIN
    assert mapview.radius_for_questions(-50) == mapview.R_MIN
    # a long topic that has asked you nothing is still a small circle: words
    # are not a size signal any more
    assert mapview.payload(g([node("a", words=100_000, questions=2)])
                           )["nodes"][0]["r"] == mapview.RQ_LO


# ── labels ──────────────────────────────────────────────────────────

def test_label_truncates_long_titles_and_keeps_short_ones():
    assert mapview.label_for("Short one") == "Short one"
    long = "Retrieval augmented generation fundamentals and evaluation"
    out = mapview.label_for(long)
    assert len(out) <= mapview.LABEL_MAX
    assert out.endswith("…")
    assert mapview.label_for("  spaced   out  ") == "spaced out"


# ── seed ────────────────────────────────────────────────────────────

def test_seed_depends_on_the_node_set_only_and_is_stable():
    assert mapview.seed_for(["b", "a"]) == mapview.seed_for(["a", "b"])
    assert mapview.seed_for(["a", "b"]) != mapview.seed_for(["a", "c"])
    # a literal, so a refactor that changes the hash is a visible change
    assert mapview.seed_for(["demo-alpha-topic", "demo-beta-topic"]) == \
        mapview.seed_for(["demo-beta-topic", "demo-alpha-topic"])
    assert 0 < mapview.seed_for(["a"]) <= 0xFFFFFFFF


# ── filtering ───────────────────────────────────────────────────────

def test_track_filter_drops_nodes_and_the_edges_touching_them():
    graph = g([node("a", track="backend"), node("b", track="dsa"),
               node("c", track="backend")],
              [{"source": "a", "target": "b", "kind": "related"},
               {"source": "a", "target": "c", "kind": "link"}])
    view = mapview.filter_graph(graph, tracks=["backend"])
    assert [n["slug"] for n in view["nodes"]] == ["a", "c"]
    assert [(e["source"], e["target"]) for e in view["edges"]] == [("a", "c")]


def test_no_tracks_selected_shows_nothing_but_none_shows_everything():
    graph = g([node("a"), node("b")],
              [{"source": "a", "target": "b", "kind": "related"}])
    assert mapview.filter_graph(graph, tracks=[])["nodes"] == []
    assert mapview.filter_graph(graph, tracks=[])["edges"] == []
    assert len(mapview.filter_graph(graph, tracks=None)["nodes"]) == 2


def test_unstudied_only_keeps_what_is_still_waiting_on_you():
    graph = g([node("a", studied=True), node("b"), node("c", studied=True)],
              [{"source": "a", "target": "b", "kind": "related"}])
    view = mapview.filter_graph(graph, unstudied_only=True)
    assert [n["slug"] for n in view["nodes"]] == ["b"]
    assert view["edges"] == []
    # the source graph is not mutated by a filter
    assert len(graph["nodes"]) == 3


# ── payload ─────────────────────────────────────────────────────────

def test_payload_carries_the_drawing_decisions():
    graph = g([node("b-topic", track="dsa", questions=9, notes=3, studied=True,
                    tags=["heap"], title="Heaps and priority queues"),
               node("a-topic", track="nonsense", questions=0)],
              [{"source": "a-topic", "target": "b-topic", "kind": "related"}])
    data = mapview.payload(graph)
    assert [n["slug"] for n in data["nodes"]] == ["a-topic", "b-topic"]  # sorted
    b = data["nodes"][1]
    assert b["color"] == mapview.TRACK_COLORS["dsa"]
    assert b["r"] == mapview.radius_for_questions(9)
    assert b["kind"] == "topic"
    assert b["label"] == "Heaps and priority queues"
    assert b["notes"] == 3 and b["studied"] is True and b["tags"] == ["heap"]
    a = data["nodes"][0]
    assert a["color"] == mapview.UNKNOWN_COLOR and a["r"] == mapview.R_MIN
    assert data["edges"] == [{"source": "a-topic", "target": "b-topic",
                              "kind": "related"}]
    assert data["seed"] == mapview.seed_for(["a-topic", "b-topic"])


def test_payload_draws_one_line_per_pair_and_no_dangling_edges():
    graph = g([node("a"), node("b")],
              [{"source": "a", "target": "b", "kind": "related"},
               {"source": "b", "target": "a", "kind": "link"},   # same pair
               {"source": "a", "target": "gone", "kind": "related"},
               {"source": "a", "target": "a", "kind": "link"}])
    data = mapview.payload(graph)
    assert data["edges"] == [{"source": "a", "target": "b", "kind": "related"}]


def test_payload_keeps_only_pins_it_can_use():
    graph = g([node("a"), node("b")])
    data = mapview.payload(graph, {"a": [12.5, -3], "b": ["x", 1],
                                   "gone": [1, 2], "c": None,
                                   "d": [float("nan"), 1]})
    assert data["pins"] == {"a": [12.5, -3.0]}


def test_payload_is_identical_for_the_same_graph():
    graph = g([node("a", questions=5), node("b", questions=2)],
              [{"source": "a", "target": "b", "kind": "related"}])
    assert mapview.payload(graph) == mapview.payload(graph)


# ── the concept layer ───────────────────────────────────────────────

def cresult(**kw):
    """A `jobscout.concepts.build` shaped result, as far as the map cares."""
    base = {"concepts": {}, "edges": [], "prereqs": {}}
    base.update(kw)
    return base


def test_with_concepts_namespaces_every_concept_and_names_its_hubs():
    graph = g([node("alpha"), node("beta")],
              [{"source": "alpha", "target": "beta", "kind": "related"}])
    out = mapview.with_concepts(graph, cresult(
        concepts={("share",): {"id": "c:share", "canonical": "Shared Idea",
                               "names": ["Shared Idea"],
                               "topics": ["alpha", "beta"]},
                  ("solo",): {"id": "c:solo", "canonical": "solo idea",
                              "names": ["solo idea"], "topics": ["alpha"]},
                  ("gone",): {"id": "c:gone", "canonical": "orphan",
                              "names": ["orphan"], "topics": ["missing"]}},
        edges=[{"source": "alpha", "target": "c:share", "kind": "concept"},
               {"source": "beta", "target": "c:share", "kind": "concept"},
               {"source": "alpha", "target": "c:solo", "kind": "concept"}],
        prereqs={"beta": ["alpha"], "alpha": []}))
    sats = {n["slug"]: n for n in out["nodes"] if mapview.is_concept(n)}
    # one node per canonical concept, `c:` namespaced, and a concept nobody
    # visible names never appears
    assert sorted(sats) == ["c:share", "c:solo"]
    assert sats["c:share"]["hubs"] == ["alpha", "beta"]
    assert sats["c:share"]["title"] == "Shared Idea"
    # topics keep their own kind, so nothing downstream has to guess
    assert all(n.get("kind") == "topic"
               for n in out["nodes"] if not mapview.is_concept(n))
    # the prerequisite is drawn from the thing you read first
    assert {"source": "alpha", "target": "beta", "kind": "prereq"} in out["edges"]
    # and the graph it was handed is untouched
    assert len(graph["nodes"]) == 2 and len(graph["edges"]) == 1


def test_payload_keeps_a_related_line_and_a_prereq_arrow_on_the_same_pair():
    """The latent break this workstream was named after: one line per pair
    drops the arrow the moment a prerequisite is also `related`, which on
    this study base is nearly always."""
    graph = g([node("alpha"), node("beta")],
              [{"source": "alpha", "target": "beta", "kind": "related"},
               {"source": "beta", "target": "alpha", "kind": "link"},
               {"source": "alpha", "target": "beta", "kind": "prereq"}])
    data = mapview.payload(graph)
    kinds = [(e["source"], e["target"], e["kind"]) for e in data["edges"]]
    assert kinds == [("alpha", "beta", "prereq"), ("alpha", "beta", "related")]


def test_payload_keeps_prereq_direction_but_still_dedupes_per_kind():
    graph = g([node("a"), node("b")],
              [{"source": "a", "target": "b", "kind": "prereq"},
               {"source": "a", "target": "b", "kind": "prereq"},   # the same
               {"source": "b", "target": "a", "kind": "prereq"}])  # not
    data = mapview.payload(graph)
    assert [(e["source"], e["target"]) for e in data["edges"]] == \
        [("a", "b"), ("b", "a")]


def test_payload_draws_concept_nodes_without_a_track_or_a_size_signal():
    graph = g([node("alpha", questions=9),
               concept("c:share", "Shared Idea", ["alpha", "gone"])],
              [{"source": "alpha", "target": "c:share", "kind": "concept"}])
    data = mapview.payload(graph)
    sat = [n for n in data["nodes"] if n["kind"] == "concept"][0]
    assert sat["slug"] == "c:share" and sat["label"] == "Shared Idea"
    assert sat["r"] == mapview.CONCEPT_R and sat["color"] == mapview.CONCEPT_COLOR
    assert sat["track"] is None and sat["studied"] is False
    # hubs are pruned to what is actually on the canvas
    assert sat["hubs"] == ["alpha"]
    # and the seed is the topic set's, so the hubs land where topic mode
    # would have put them
    assert data["seed"] == mapview.seed_for(["alpha"])


def test_a_concept_is_never_pinned():
    graph = g([node("alpha"), concept("c:share", "Shared", ["alpha"])])
    data = mapview.payload(graph, {"alpha": [3, 4], "c:share": [5, 6]})
    assert data["pins"] == {"alpha": [3.0, 4.0]}


def test_filter_keeps_a_concept_while_one_of_its_topics_is_on_the_canvas():
    graph = g([node("a", track="backend"), node("b", track="dsa"),
               concept("c:both", "Both", ["a", "b"]),
               concept("c:only-b", "Only B", ["b"])],
              [{"source": "a", "target": "c:both", "kind": "concept"},
               {"source": "b", "target": "c:both", "kind": "concept"},
               {"source": "b", "target": "c:only-b", "kind": "concept"}])
    view = mapview.filter_graph(graph, tracks=["backend"])
    assert sorted(n["slug"] for n in view["nodes"]) == ["a", "c:both"]
    # the shared concept survives on its remaining hub, and the edge into the
    # topic that just left does not
    assert [(e["source"], e["target"]) for e in view["edges"]] == \
        [("a", "c:both")]
    # every hub gone, concept gone
    assert mapview.filter_graph(graph, tracks=["system-design"])["nodes"] == []


def test_unstudied_only_does_not_study_a_concept_out_of_existence():
    graph = g([node("a", studied=True), node("b"),
               concept("c:both", "Both", ["a", "b"]),
               concept("c:only-a", "Only A", ["a"])])
    view = mapview.filter_graph(graph, unstudied_only=True)
    assert sorted(n["slug"] for n in view["nodes"]) == ["b", "c:both"]


# ── B4's Python half: the component ships nothing it has to fetch ───

def test_component_strings_reference_no_external_resource():
    blob = mapview._HTML + mapview._CSS + mapview._JS
    for needle in ("https://", "http://", "//cdn", "fetch(", "import("):
        assert needle not in blob, needle


# ── the live loop's house rules, as far as Python can see them ──────
# The simulation itself is e2e territory. What is worth pinning down here is
# the handful of promises that are easy to delete by accident in a refactor
# and expensive to notice in a browser: a frame loop that is never cancelled
# leaks one animation per rerun, and one that ignores the OS motion setting
# is a bug you only hear about from someone it hurts.

def test_the_frame_loop_is_cancelled_and_paused():
    js = mapview._JS
    assert "requestAnimationFrame" in js
    assert "cancelAnimationFrame" in js, "the frame loop must be cancellable"
    assert "document.hidden" in js, "a hidden tab must not be simulated"
    assert "visibilitychange" in js
    # the cleanup function the component returns is what Streamlit calls on
    # unmount; it has to take the listeners with it
    tail = js[js.rindex("return () => {"):]
    for gone in ("stop()", "removeEventListener"):
        assert gone in tail, gone


def test_reduced_motion_is_respected():
    assert "(prefers-reduced-motion: reduce)" in mapview._JS
    assert "prefers-reduced-motion: reduce" in mapview._CSS


def test_the_hub_settle_never_sees_a_satellite_or_a_prerequisite():
    """B3a in the one place Python can still see it. The hub layout has to be
    the same picture in both modes, which needs two things to stay true of
    the JavaScript: the settle runs over the hub array, and a `prereq` edge
    never becomes a spring (it is drawn with a head on it and pulls nothing).
    Both are one careless edit away from being untrue and neither is visible
    until you switch modes and watch the whole map slide."""
    js = mapview._JS
    assert 'if (e.kind === "prereq" || e.kind === "concept") continue' in js
    assert "accumulate(SH, HL, hfx, hfy)" in js
    assert "integrate(PH, HL, hfx, hfy, alpha, s)" in js


def test_the_component_still_ships_its_own_arrowhead():
    js = mapview._JS
    assert 'el("marker"' in js and "orient" in js
    assert 'marker-end' in js
    assert ".jsm-prereq" in mapview._CSS and ".jsm-concept" in mapview._CSS


def test_the_settled_layout_is_published_separately_from_the_live_one():
    """`cx`/`cy` drift; `data-x0`/`data-y0` are the layout of record, which
    is what the e2e tests measure and what the drawing is framed around."""
    assert '"data-x0": S[i].x' in mapview._JS
    assert '"data-y0": S[i].y' in mapview._JS
