"""Hotel guest rooms had no zone category, so they fell to `other`.

Found 2026-08-08 by running the pipeline against real hospitality plans pulled from
public CAD repositories, rather than against the five clean plans in the golden set.

The consequence was not cosmetic. `ATTRACTION` is keyed by category with a default of
1.0, and dining sits at 5.0 — so on a hotel guest floor the pedestrian simulation routed
guests away from the single largest destination on the plate, at a fifth of the pull it
should have had. That is the same failure as the café that was typed `other`, with one
difference that made it worse: no prompt fix could have corrected it, because the right
answer did not exist in the taxonomy.

Guest rooms attract fully and score not at all — they are destinations the simulation
must route to, but how heavily one is occupied says nothing about whether the floor is
well laid out, and by area they would drown out the circulation the score exists to
measure.
"""

import pytest

from app import report_html
from app.pedestrian import ATTRACTION
from app.pipeline import flow_efficiency_score
from app.solver import USABLE_SHARE
from meyraki_contracts import (
    SCORE_EXCLUDED,
    FlowReport,
    Point,
    Track,
    Zone,
    ZoneCategory,
    ZoneFlow,
    ZoneGraph,
)


def _zone(zid: str, category: ZoneCategory, x0: float, x1: float) -> Zone:
    return Zone(
        id=zid, category=category, label=zid, confidence=0.9,
        polygon=[Point(x=x0, y=0.0), Point(x=x1, y=0.0), Point(x=x1, y=1.0), Point(x=x0, y=1.0)],
    )


def _flow(*pairs: tuple[str, float]) -> FlowReport:
    return FlowReport(
        track=Track.SIMULATED,
        zone_flows=[ZoneFlow(zone_id=zid, intensity=i) for zid, i in pairs],
        bottlenecks=[], dead_zones=[], notes=["fixture"],
    )


def test_a_guest_room_has_a_category_of_its_own():
    assert ZoneCategory.GUESTROOM.value == "guestroom"


def test_a_guest_room_is_not_left_to_the_default_attraction():
    """The bug itself: no entry meant ATTRACTION.get(..., 1.0) against dining's 5.0."""
    assert ATTRACTION.get(ZoneCategory.GUESTROOM) == 5.0
    assert ATTRACTION[ZoneCategory.GUESTROOM] == ATTRACTION[ZoneCategory.DINING]


def test_a_guest_room_has_its_own_usable_share():
    """A bed, a wardrobe and an ensuite leave less open floor than a dining room."""
    assert USABLE_SHARE.get(ZoneCategory.GUESTROOM) == 0.50


def test_a_guest_room_is_excluded_from_the_headline_score():
    assert ZoneCategory.GUESTROOM in SCORE_EXCLUDED


def test_guest_rooms_do_not_move_the_score():
    """Adding rooms to a plan must not change how well its shared space reads.

    Before the exclusion they were scored like any guest zone, so a hotel that added a
    wing would see its circulation score drift for a reason that had nothing to do with
    circulation.
    """
    shared = [_zone("lobby", ZoneCategory.LOBBY, 0.0, 0.5),
              _zone("dining", ZoneCategory.DINING, 0.5, 1.0)]
    without = ZoneGraph(zones=shared, adjacency=[("lobby", "dining")], entrances=["lobby"])
    flow_without = _flow(("lobby", 1.0), ("dining", 0.5))

    rooms = [_zone(f"room{i}", ZoneCategory.GUESTROOM, i / 8, (i + 1) / 8) for i in range(8)]
    with_rooms = ZoneGraph(zones=shared + rooms, adjacency=[("lobby", "dining")],
                           entrances=["lobby"])
    flow_with = _flow(("lobby", 1.0), ("dining", 0.5), *[(f"room{i}", 0.9) for i in range(8)])

    assert flow_efficiency_score(without, flow_without) == flow_efficiency_score(
        with_rooms, flow_with
    )


def test_a_floor_of_only_guest_rooms_scores_nothing_rather_than_zero():
    """A guest floor legitimately has nothing left to score. It must not read as 0.0,
    which a client would take as 'this floor performs terribly'."""
    rooms = [_zone(f"room{i}", ZoneCategory.GUESTROOM, i / 4, (i + 1) / 4) for i in range(4)]
    graph = ZoneGraph(zones=rooms, adjacency=[], entrances=["room0"])
    assert flow_efficiency_score(graph, _flow(*[(f"room{i}", 0.8) for i in range(4)])) is None


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_guest_room_is_named_in_both_report_languages(lang):
    """An untranslated category prints an English enum value under an Arabic header."""
    assert report_html.CATEGORY_LABELS[lang]["guestroom"]
    assert report_html.CATEGORY_LABELS["ar"]["guestroom"] != "guestroom"
