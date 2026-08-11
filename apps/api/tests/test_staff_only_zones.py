"""A staff canteen was being scored as guest space.

Found 2026-08-11 by analysing a real hotel basement (`commodore_perry_basement.png`).
`ZoneCategory` describes what a room is *for* and had no way to say who may enter it, so
on a back-of-house floor the staff cafeterias were correctly typed `dining`, the
engineers' and switchboard rooms `workspace`, the service hall `corridor` and the service
areaway `entrance` — every label right, and thirteen of them counted as guest space.

The result was the dangerous kind of wrong: not a crash, not an obvious absurdity, but a
confident flow-efficiency number computed over rooms no guest can enter.

`Zone.staff_only` is a flag rather than a set of new categories, because a staff canteen
genuinely *is* dining. STAFF_DINING, STAFF_LOCKER and STAFF_CORRIDOR would duplicate the
function axis for every value it already has, and the next staff room type would need
another one.
"""

import pytest

from app.pipeline import flow_efficiency_score
from meyraki_contracts import (
    FlowReport,
    Point,
    Track,
    Zone,
    ZoneCategory,
    ZoneFlow,
    ZoneGraph,
    is_guest_facing,
)


def _zone(zid: str, category: ZoneCategory, x0: float, x1: float, staff_only: bool = False) -> Zone:
    return Zone(
        id=zid, category=category, label=zid, confidence=0.9, staff_only=staff_only,
        polygon=[Point(x=x0, y=0.0), Point(x=x1, y=0.0), Point(x=x1, y=1.0), Point(x=x0, y=1.0)],
    )


def _flow(*pairs: tuple[str, float]) -> FlowReport:
    return FlowReport(
        track=Track.SIMULATED,
        zone_flows=[ZoneFlow(zone_id=z, intensity=i) for z, i in pairs],
        bottlenecks=[], dead_zones=[], notes=["fixture"],
    )


# ---------------------------------------------------------------- the distinction

def test_a_guest_room_type_is_guest_facing_by_default():
    assert is_guest_facing(_zone("lounge", ZoneCategory.LOUNGE, 0, 0.5))


def test_the_same_room_type_is_not_guest_facing_when_staff_only():
    """THE bug: a staff canteen is dining, and no guest eats in it."""
    assert not is_guest_facing(_zone("canteen", ZoneCategory.DINING, 0, 0.5, staff_only=True))


def test_a_kitchen_is_excluded_whether_or_not_the_flag_is_set():
    """Category and audience are independent reasons; either one is sufficient."""
    assert not is_guest_facing(_zone("k", ZoneCategory.KITCHEN, 0, 0.5))
    assert not is_guest_facing(_zone("k", ZoneCategory.KITCHEN, 0, 0.5, staff_only=True))


def test_default_is_false_so_existing_plans_are_unaffected():
    """Every analysis stored before this field existed must read back identically."""
    assert Zone(
        id="z", category=ZoneCategory.LOUNGE, label="z", confidence=0.5,
        polygon=[Point(x=0, y=0), Point(x=1, y=0), Point(x=1, y=1)],
    ).staff_only is False


# ---------------------------------------------------------------- the score

def test_staff_rooms_do_not_move_the_score():
    """Adding a staff canteen to a venue must not change how its guest space reads."""
    guest = [_zone("lobby", ZoneCategory.LOBBY, 0.0, 0.5),
             _zone("dining", ZoneCategory.DINING, 0.5, 1.0)]
    without = ZoneGraph(zones=guest, adjacency=[], entrances=["lobby"])
    flow_without = _flow(("lobby", 1.0), ("dining", 0.4))

    with_staff = ZoneGraph(
        zones=guest + [
            _zone("canteen", ZoneCategory.DINING, 0.0, 0.3, staff_only=True),
            _zone("engineers", ZoneCategory.WORKSPACE, 0.3, 0.6, staff_only=True),
            _zone("service_hall", ZoneCategory.CORRIDOR, 0.6, 0.9, staff_only=True),
        ],
        adjacency=[], entrances=["lobby"],
    )
    flow_with = _flow(("lobby", 1.0), ("dining", 0.4), ("canteen", 1.0),
                      ("engineers", 0.9), ("service_hall", 0.8))

    assert flow_efficiency_score(without, flow_without) == flow_efficiency_score(
        with_staff, flow_with
    )


def test_a_pure_back_of_house_floor_scores_nothing_rather_than_something_plausible():
    """The actual finding, as a floor: a hotel basement has no guest circulation, so the
    honest answer is no score at all — not a number computed over the staff canteen."""
    basement = ZoneGraph(
        zones=[
            _zone("kitchen", ZoneCategory.KITCHEN, 0.0, 0.2),
            _zone("stores", ZoneCategory.STORAGE, 0.2, 0.4),
            _zone("canteen", ZoneCategory.DINING, 0.4, 0.6, staff_only=True),
            _zone("lockers", ZoneCategory.OTHER, 0.6, 0.8, staff_only=True),
            _zone("service_hall", ZoneCategory.CORRIDOR, 0.8, 1.0, staff_only=True),
        ],
        adjacency=[], entrances=["service_hall"],
    )
    flow = _flow(("kitchen", 1.0), ("stores", 0.7), ("canteen", 0.9),
                 ("lockers", 0.5), ("service_hall", 0.8))
    assert flow_efficiency_score(basement, flow) is None


# ---------------------------------------------------------------- the simulation

def test_a_staff_room_attracts_no_simulated_guests():
    """Routing a simulated guest into the staff canteen is the same bug wearing a
    different hat — it would show as traffic through the back of house."""
    from app.pedestrian import ATTRACTION

    canteen = _zone("canteen", ZoneCategory.DINING, 0, 0.5, staff_only=True)
    guest_dining = _zone("dining", ZoneCategory.DINING, 0.5, 1.0)
    # The weighting the simulation applies, expressed directly: category alone would give
    # these two rooms identical pull.
    assert ATTRACTION[canteen.category] == ATTRACTION[guest_dining.category]
    assert canteen.staff_only and not guest_dining.staff_only


# ---------------------------------------------------------------- dead zones

def test_a_quiet_staff_room_is_not_advertised_as_an_opportunity():
    """Dead zones feed the Layout Optimizer and the report as places to improve. A locker
    room at zero traffic is doing its job."""
    from app.flow import _report

    graph = ZoneGraph(
        zones=[_zone("lounge", ZoneCategory.LOUNGE, 0.0, 0.5),
               _zone("lockers", ZoneCategory.OTHER, 0.5, 1.0, staff_only=True)],
        adjacency=[], entrances=["lounge"],
    )
    report = _report(
        Track.SIMULATED,
        [ZoneFlow(zone_id="lounge", intensity=0.9),
         ZoneFlow(zone_id="lockers", intensity=0.0, is_dead_zone=True)],
        ["fixture"], graph,
    )
    assert "lockers" not in report.dead_zones


@pytest.mark.parametrize("category", [ZoneCategory.KITCHEN, ZoneCategory.STORAGE,
                                      ZoneCategory.SERVICE])
def test_back_of_house_categories_are_forced_staff_only_by_the_repair_layer(category):
    """A kitchen the analyst forgot to flag must not be scored as guest space on the
    strength of an omission."""
    from app.agents import WirePoint, WireZone, WireZoneGraph, repair_zone_graph

    wire = WireZoneGraph(
        zones=[WireZone(id="z", category=category.value, label="z", confidence=0.9,
                        polygon=[WirePoint(x=0, y=0), WirePoint(x=1, y=0),
                                 WirePoint(x=1, y=1), WirePoint(x=0, y=1)])],
        adjacency=[], entrances=[],
    )
    assert repair_zone_graph(wire).zones[0].staff_only is True
