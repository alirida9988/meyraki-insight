"""JuPedSim guest-flow simulation (docs/04-REUSE-MAP §3).

Runs offline — JuPedSim is a local C++ engine, no network, no model calls.
"""

from app import flow, pedestrian
from app.agents import WireAdjacency, WireZoneGraph, repair_zone_graph
from meyraki_contracts import Track
from tests.test_agents import _wz


def _venue():
    """entrance → lobby → {cafe, lounge}, plus back-of-house: every route
    to a destination has to cross the lobby."""
    return repair_zone_graph(
        WireZoneGraph(
            zones=[
                _wz("entrance", category="entrance",
                    points=[(0.0, 0.75), (0.30, 0.75), (0.30, 1.0), (0.0, 1.0)]),
                _wz("lobby", category="lobby",
                    points=[(0.0, 0.30), (0.55, 0.30), (0.55, 0.75), (0.0, 0.75)]),
                _wz("cafe", category="dining",
                    points=[(0.55, 0.30), (1.0, 0.30), (1.0, 0.80), (0.55, 0.80)]),
                _wz("lounge", category="lounge",
                    points=[(0.0, 0.0), (0.55, 0.0), (0.55, 0.30), (0.0, 0.30)]),
                _wz("kitchen", category="kitchen",
                    points=[(0.55, 0.80), (1.0, 0.80), (1.0, 1.0), (0.55, 1.0)]),
            ],
            adjacency=[
                WireAdjacency(a="entrance", b="lobby"),
                WireAdjacency(a="lobby", b="cafe"),
                WireAdjacency(a="lobby", b="lounge"),
            ],
            entrances=["entrance"],
        )
    )


def test_simulation_finds_the_funnel_zone():
    """The value decay cannot express: the lobby is busiest because every
    journey crosses it, not because it sits nearest the entrance."""
    intensities, notes, reason = pedestrian.simulate(_venue())
    assert intensities is not None and reason is None
    assert intensities["lobby"] == 1.0
    assert intensities["lobby"] > intensities["entrance"]  # decay would invert this
    assert intensities["cafe"] > 0.1  # the attractive destination gets traffic
    assert "JuPedSim" in notes[0]


def test_every_zone_gets_a_value_including_unvisited_ones():
    intensities, _notes, _reason = pedestrian.simulate(_venue())
    graph = _venue()
    assert set(intensities) == {z.id for z in graph.zones}
    assert intensities["kitchen"] == 0.0  # back-of-house is never a destination


def test_simulation_is_deterministic():
    """A client report must be reproducible from the same plan."""
    first, _n1, _r1 = pedestrian.simulate(_venue())
    second, _n2, _r2 = pedestrian.simulate(_venue())
    assert first == second


def test_scale_assumption_is_reported():
    _intensities, notes, _reason = pedestrian.simulate(_venue())
    assert any("nominal" in n and "long edge" in n for n in notes)


def test_unusable_geometry_degrades_with_a_reason_instead_of_raising():
    single = repair_zone_graph(WireZoneGraph(zones=[_wz("only")], adjacency=[], entrances=[]))
    intensities, _notes, reason = pedestrian.simulate(single)
    assert intensities is None
    assert reason and "walkable" in reason


def test_flow_simulated_uses_pedestrian_dynamics_and_says_so():
    report = flow.simulated(_venue())
    assert report.track == Track.SIMULATED
    assert any("JuPedSim" in n for n in report.notes)
    busiest = max(report.zone_flows, key=lambda f: f.intensity)
    assert busiest.zone_id == "lobby" and busiest.is_bottleneck


def test_flow_simulated_falls_back_and_states_the_real_reason(monkeypatch):
    """The note used to claim the geometry was unusable whatever the actual cause."""
    monkeypatch.setattr(
        pedestrian, "simulate",
        lambda graph: (None, [], "no guest-facing destination was detected on the plan"),
    )
    report = flow.simulated(_venue())
    assert report.track == Track.SIMULATED
    assert any("distance decay" in n for n in report.notes)
    assert any("no guest-facing destination" in n for n in report.notes)
    assert not any("walkable surface" in n for n in report.notes)
    assert {f.zone_id for f in report.zone_flows} == {z.id for z in _venue().zones}
