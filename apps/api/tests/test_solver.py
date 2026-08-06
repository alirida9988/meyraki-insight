"""OR-Tools CP-SAT layout feasibility (docs/04-REUSE-MAP §2)."""

from app.agents import WireAdjacency, WireZoneGraph, repair_zone_graph
from app.solver import check_scenario, validate
from meyraki_contracts import LayoutMove, LayoutProposals, Objective, Scenario
from tests.test_agents import _wz


def _graph():
    return repair_zone_graph(
        WireZoneGraph(
            zones=[
                _wz("lobby", category="lobby", points=[(0, 0), (0.5, 0), (0.5, 1), (0, 1)]),
                _wz("entrance", category="entrance",
                    points=[(0.5, 0), (1, 0), (1, 0.2), (0.5, 0.2)]),
                _wz("cafe", category="dining", points=[(0.5, 0.2), (1, 0.2), (1, 1), (0.5, 1)]),
            ],
            adjacency=[WireAdjacency(a="entrance", b="lobby"), WireAdjacency(a="lobby", b="cafe")],
            entrances=["entrance"],
        )
    )


def _scenario(*moves) -> Scenario:
    return Scenario(
        id="s", name="S", confidence=0.7, predicted_effects={},
        moves=[LayoutMove(description=d, zone_ids=z, rationale="r", footprint_pct=f)
               for d, z, f in moves],
    )


def test_moves_within_usable_area_are_feasible():
    ok, notes = check_scenario(_scenario(("banquette", ["lobby"], 30.0),
                                         ("planter", ["lobby"], 20.0)), _graph())
    assert ok and "fit within every zone" in notes[0]


def test_overcommitted_zone_is_infeasible_and_names_the_conflict():
    ok, notes = check_scenario(
        _scenario(("banquette", ["lobby"], 30.0), ("planter", ["lobby"], 20.0),
                  ("stage", ["lobby"], 40.0)), _graph())
    assert not ok
    assert "cannot coexist" in notes[0]
    assert any("%" in n for n in notes[1:])  # the offending move is named with its demand


def test_egress_zones_keep_more_clear_floor():
    """35% usable in an entrance vs 60% in a lobby — the same move flips verdict."""
    graph = _graph()
    assert check_scenario(_scenario(("kiosk", ["lobby"], 50.0),), graph)[0] is True
    assert check_scenario(_scenario(("kiosk", ["entrance"], 50.0),), graph)[0] is False


def test_zero_footprint_moves_always_fit():
    ok, notes = check_scenario(_scenario(("wayfinding signage", ["entrance"], 0.0),), _graph())
    assert ok and "nothing to contest" in notes[0]


def test_move_spanning_zones_is_bound_by_the_tightest():
    """Genuinely combinatorial: the same move is judged against every zone it touches."""
    ok, _ = check_scenario(_scenario(("shared island", ["lobby", "entrance"], 40.0),), _graph())
    assert ok is False


def test_validate_stamps_every_scenario():
    proposals = LayoutProposals(
        objectives=[Objective.GUEST_FLOW],
        scenarios=[_scenario(("ok", ["cafe"], 20.0)), _scenario(("too big", ["entrance"], 90.0))],
    )
    out = validate(proposals, _graph())
    assert [s.solver_feasible for s in out.scenarios] == [True, False]
    assert all(s.solver_notes for s in out.scenarios)


def test_unknown_zone_ids_do_not_crash_the_solver():
    ok, _ = check_scenario(_scenario(("ghost move", ["nonexistent"], 50.0),), _graph())
    assert ok is True  # nothing real is consumed, so nothing is contested
