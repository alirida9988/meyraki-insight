"""Zone Analyst repair layer — the deterministic guarantee behind the vision model.
No API calls: these test the proposal→contract validation/repair path only.
"""

from app.agents import WireAdjacency, WirePoint, WireZone, WireZoneGraph, repair_zone_graph
from meyraki_contracts import ZoneCategory


def _wz(zid, category="lobby", points=None, confidence=0.9):
    points = points or [(0, 0), (1, 0), (1, 1), (0, 1)]
    return WireZone(
        id=zid,
        category=category,
        label=zid.title(),
        polygon=[WirePoint(x=x, y=y) for x, y in points],
        confidence=confidence,
    )


def test_clamps_out_of_range_coordinates_and_confidence():
    graph = repair_zone_graph(
        WireZoneGraph(
            zones=[_wz("lobby", points=[(-0.2, 0), (1.4, 0), (1.0, 1.7)], confidence=1.8)],
            adjacency=[],
            entrances=[],
        )
    )
    zone = graph.zones[0]
    assert all(0 <= p.x <= 1 and 0 <= p.y <= 1 for p in zone.polygon)
    assert zone.confidence == 1.0


def test_unknown_category_coerced_to_other():
    graph = repair_zone_graph(
        WireZoneGraph(zones=[_wz("spa", category="wellness sanctuary")], adjacency=[], entrances=[])
    )
    assert graph.zones[0].category == ZoneCategory.OTHER


def test_degenerate_polygons_and_dangling_refs_dropped():
    graph = repair_zone_graph(
        WireZoneGraph(
            zones=[_wz("lobby"), _wz("line", points=[(0, 0), (1, 1)])],
            adjacency=[WireAdjacency(a="lobby", b="line"), WireAdjacency(a="lobby", b="ghost")],
            entrances=["lobby", "ghost"],
        )
    )
    assert [z.id for z in graph.zones] == ["lobby"]
    assert graph.adjacency == []
    assert graph.entrances == ["lobby"]


def test_duplicate_ids_deduped_and_normalized():
    graph = repair_zone_graph(
        WireZoneGraph(
            zones=[_wz("Main Lobby"), _wz("main_lobby")],
            adjacency=[],
            entrances=["Main Lobby"],
        )
    )
    ids = [z.id for z in graph.zones]
    assert len(ids) == len(set(ids)) == 2
    assert "main_lobby" in ids
    assert graph.entrances == ["main_lobby"]


def test_symmetric_adjacency_deduped():
    graph = repair_zone_graph(
        WireZoneGraph(
            zones=[_wz("a"), _wz("b")],
            adjacency=[WireAdjacency(a="a", b="b"), WireAdjacency(a="b", b="a"), WireAdjacency(a="a", b="a")],
            entrances=[],
        )
    )
    assert graph.adjacency == [("a", "b")]


# ---------------------------------------------------------------- layout repair

from app.agents import WireLayout, WireMove, WireScenario, repair_layout
from meyraki_contracts import Objective


def _graph_two_zones():
    from app.agents import WireZoneGraph
    return repair_zone_graph(WireZoneGraph(zones=[_wz("lobby"), _wz("cafe")], adjacency=[], entrances=[]))


def _ws(sid, zone_ids, name="Scenario"):
    return WireScenario(
        id=sid,
        name=name,
        moves=[WireMove(description="Move the desk", zone_ids=zone_ids, rationale="bottleneck")],
        predicted_effects={"guest_flow": "+10% (est.)"},
        confidence=0.7,
    )


def test_layout_moves_with_unknown_zones_dropped():
    graph = _graph_two_zones()
    wire = WireLayout(scenarios=[_ws("a", ["lobby", "ghost"]), _ws("b", ["ghost"])])
    result = repair_layout(wire, graph, [Objective.GUEST_FLOW])
    assert [s.id for s in result.scenarios] == ["a"]
    assert result.scenarios[0].moves[0].zone_ids == ["lobby"]
    assert result.scenarios[0].solver_feasible is False


def test_layout_all_invalid_raises():
    import pytest as _pytest
    graph = _graph_two_zones()
    with _pytest.raises(RuntimeError):
        repair_layout(WireLayout(scenarios=[_ws("a", ["ghost"])]), graph, [Objective.GUEST_FLOW])


def test_layout_caps_at_three_scenarios():
    graph = _graph_two_zones()
    wire = WireLayout(scenarios=[_ws(f"s{i}", ["lobby"]) for i in range(5)])
    assert len(repair_layout(wire, graph, [Objective.GUEST_FLOW]).scenarios) == 3


# ---------------------------------------------------------------- business math

def test_flow_efficiency_score_excludes_back_of_house():
    from app.flow import simulated
    from app.pipeline import flow_efficiency_score
    from app.agents import WireZoneGraph

    graph = repair_zone_graph(WireZoneGraph(
        zones=[
            _wz("lobby", category="lobby", points=[(0, 0), (0.5, 0), (0.5, 1), (0, 1)]),
            _wz("kitchen", category="kitchen", points=[(0.5, 0), (1, 0), (1, 1), (0.5, 1)]),
        ],
        adjacency=[WireAdjacency(a="lobby", b="kitchen")],
        entrances=["lobby"],
    ))
    flow = simulated(graph)  # lobby=1.0, kitchen=0.75
    # kitchen is back-of-house -> score reflects lobby only
    assert flow_efficiency_score(graph, flow) == 100.0


# Review findings #2 and #3 (2026-08-06)

def test_empty_zone_graph_fails_at_the_source():
    from app.agents import WireZoneGraph
    import pytest as _pytest

    line_only = WireZoneGraph(
        zones=[_wz("line", points=[(0, 0), (1, 1)])], adjacency=[], entrances=[]
    )
    with _pytest.raises(RuntimeError, match="no usable zones"):
        repair_zone_graph(line_only)


def test_palette_deduped_normalized_and_validated():
    from app.agents import clean_palette
    import pytest as _pytest

    assert clean_palette(["#aaaaaa", "AAAAAA", "#BBBBBB", "#cccccc", "junk", "#FFF"]) == [
        "#AAAAAA", "#BBBBBB", "#CCCCCC",
    ]
    with _pytest.raises(RuntimeError):
        clean_palette(["#AAAAAA", "#AAAAAA", "nope"])
