import pytest
from pydantic import ValidationError

from meyraki_contracts import (
    ALL_CONTRACTS,
    Point,
    Zone,
    ZoneCategory,
    ZoneGraph,
    json_schemas,
)


def _zone(zid: str) -> Zone:
    return Zone(
        id=zid,
        category=ZoneCategory.LOBBY,
        label="Lobby",
        polygon=[Point(x=0, y=0), Point(x=1, y=0), Point(x=1, y=1)],
        confidence=0.9,
    )


def test_zone_graph_accepts_consistent_references():
    g = ZoneGraph(zones=[_zone("a"), _zone("b")], adjacency=[("a", "b")], entrances=["a"])
    assert len(g.zones) == 2


def test_zone_graph_rejects_unknown_adjacency():
    with pytest.raises(ValidationError):
        ZoneGraph(zones=[_zone("a")], adjacency=[("a", "ghost")])


def test_zone_graph_rejects_duplicate_ids():
    with pytest.raises(ValidationError):
        ZoneGraph(zones=[_zone("a"), _zone("a")])


def test_every_contract_exports_json_schema():
    schemas = json_schemas()
    assert set(schemas) == set(ALL_CONTRACTS)
    assert all("properties" in s for s in schemas.values())
