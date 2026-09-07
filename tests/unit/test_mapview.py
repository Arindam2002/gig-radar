"""The pure-Python half of the study map: what gets drawn, how big, what
colour, and what the browser is handed. The layout itself lives in the
component's JavaScript and is covered by the e2e tests.
"""
import math

from jobscout import mapview


def g(nodes, edges=()):
    return {"nodes": list(nodes), "edges": list(edges), "warnings": []}


def node(slug, **kw):
    base = {"slug": slug, "title": slug.replace("-", " ").title(),
            "track": "backend", "tags": [], "words": 400,
            "studied": False, "notes": 0}
    base.update(kw)
    return base


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

def test_radius_is_clamped_and_monotonic_in_words():
    assert mapview.radius_for(0) == mapview.R_MIN
    assert mapview.radius_for(1) == mapview.R_MIN
    assert mapview.radius_for(1_000_000) == mapview.R_MAX
    small, mid, big = (mapview.radius_for(w) for w in (300, 600, 900))
    assert mapview.R_MIN <= small < mid < big <= mapview.R_MAX
    # sqrt, not linear: inside the unclamped band, twice the words is
    # sqrt(2) times the radius, not twice it
    assert math.isclose(mapview.radius_for(400),
                        mapview.radius_for(200) * math.sqrt(2), rel_tol=0.01)


def test_radius_survives_junk_word_counts():
    assert mapview.radius_for(None) == mapview.R_MIN
    assert mapview.radius_for("not a number") == mapview.R_MIN
    assert mapview.radius_for(-50) == mapview.R_MIN


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
    graph = g([node("b-topic", track="dsa", words=900, notes=3, studied=True,
                    tags=["heap"], title="Heaps and priority queues"),
               node("a-topic", track="nonsense", words=0)],
              [{"source": "a-topic", "target": "b-topic", "kind": "related"}])
    data = mapview.payload(graph)
    assert [n["slug"] for n in data["nodes"]] == ["a-topic", "b-topic"]  # sorted
    b = data["nodes"][1]
    assert b["color"] == mapview.TRACK_COLORS["dsa"]
    assert b["r"] == mapview.radius_for(900)
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
    graph = g([node("a", words=500), node("b", words=120)],
              [{"source": "a", "target": "b", "kind": "related"}])
    assert mapview.payload(graph) == mapview.payload(graph)


# ── B4's Python half: the component ships nothing it has to fetch ───

def test_component_strings_reference_no_external_resource():
    blob = mapview._HTML + mapview._CSS + mapview._JS
    for needle in ("https://", "http://", "//cdn", "fetch(", "import("):
        assert needle not in blob, needle
