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
