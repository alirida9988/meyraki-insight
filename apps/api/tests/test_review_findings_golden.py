"""Regression tests for the adversarial review of the golden-set branch (2026-08-06).

One test per confirmed finding, each named for the defect it pins down.
"""

import json

import pydantic
import pytest

from app import agents, flow, report_html, settings
from app.pipeline import flow_efficiency_score, step_business
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


def _sq(zid, cat, x0, y0, x1, y1):
    return Zone(
        id=zid, category=cat, label=zid.title(), confidence=0.9,
        polygon=[Point(x=x0, y=y0), Point(x=x1, y=y0), Point(x=x1, y=y1), Point(x=x0, y=y1)],
    )


def _venue(back_category):
    """Three guest zones plus one zone whose category is the variable under test.
    The variable zone owns the traffic peak — that is the case that used to break."""
    return ZoneGraph(
        zones=[
            _sq("entrance", ZoneCategory.ENTRANCE, 0.0, 0.0, 0.25, 0.5),
            _sq("lobby", ZoneCategory.LOBBY, 0.25, 0.0, 0.5, 0.5),
            _sq("cafe", ZoneCategory.DINING, 0.5, 0.0, 0.75, 0.5),
            _sq("back", back_category, 0.75, 0.0, 1.0, 0.5),
        ],
        adjacency=[("entrance", "lobby"), ("lobby", "cafe"), ("cafe", "back")],
        entrances=["entrance"],
    )


CSV = (
    b"zone_name,timestamp,traffic_count\n"
    b"entrance,2026-01-01 08:00,200\n"
    b"lobby,2026-01-01 08:00,300\n"
    b"cafe,2026-01-01 08:00,250\n"
    b"back,2026-01-01 08:00,1000\n"
)


def test_headline_score_does_not_move_when_an_excluded_zone_is_retyped():
    """CRITICAL: intensities are normalised against the busiest zone on the whole
    plan, but the score covers guest-facing zones only. Re-typing one excluded zone
    moved the client's headline number ~19 points with every intensity identical."""
    scores = {}
    for category in (ZoneCategory.OTHER, ZoneCategory.KITCHEN, ZoneCategory.RESTROOM):
        graph = _venue(category)
        report = flow.data_driven(graph, CSV)
        intensities = {f.zone_id: f.intensity for f in report.zone_flows}
        assert intensities["back"] == 1.0, "the excluded zone must own the peak here"
        scores[category] = flow_efficiency_score(graph, report)

    # OTHER is scored; kitchen and restroom are not — so OTHER may legitimately differ.
    # What must not differ is the score between two *excluded* categories.
    assert scores[ZoneCategory.KITCHEN] == scores[ZoneCategory.RESTROOM]
    # and the guest zones must not be dragged toward zero by a hot excluded neighbour
    assert scores[ZoneCategory.KITCHEN] > 50


def test_back_of_house_is_never_reported_as_a_dead_zone_to_activate():
    """dead_zones feeds the Layout Optimizer and Report Writer, both briefed to cite
    them as opportunities. A kitchen at zero traffic is doing its job."""
    graph = _venue(ZoneCategory.KITCHEN)
    csv = CSV.replace(b"back,2026-01-01 08:00,1000", b"back,2026-01-01 08:00,0")
    report = flow.data_driven(graph, csv)
    assert "back" not in report.dead_zones
    # but the heatmap still needs every zone
    assert "back" in {f.zone_id for f in report.zone_flows}


def test_footfall_rows_never_join_on_a_category_name():
    """A CSV row named "storage" used to land on whichever zone was typed storage
    first, so measured traffic could be attributed to the wrong room entirely."""
    graph = ZoneGraph(
        zones=[
            _sq("suite_401", ZoneCategory.STORAGE, 0.0, 0.0, 0.5, 0.5),   # sorts first
            _sq("storage", ZoneCategory.STORAGE, 0.5, 0.0, 1.0, 0.5),     # the real one
        ],
        adjacency=[("suite_401", "storage")],
        entrances=["suite_401"],
    )
    report = flow.data_driven(graph, b"zone_name,timestamp,traffic_count\nstorage,2026-01-01 08:00,900\n")
    by_zone = {f.zone_id: f.intensity for f in report.zone_flows}
    assert by_zone["storage"] == 1.0
    assert by_zone["suite_401"] == 0.0


def test_assumptions_name_every_excluded_category():
    """"back-of-house excluded" did not say which types, while that exclusion moves
    the number by double digits."""
    graph = _venue(ZoneCategory.KITCHEN)
    report = flow.data_driven(graph, CSV)

    class _Ctx:
        outputs = {"zones": graph.model_dump(), "flow": json.loads(report.model_dump_json())}
        session = None
        analysis = None

    case = step_business(_Ctx())
    text = " ".join(a.statement for a in case.assumptions)
    for category in SCORE_EXCLUDED:
        assert category.value in text, f"{category.value} is excluded but never disclosed"


def test_report_translates_zone_categories_for_arabic():
    """The Arabic column header was translated while every cell under it printed an
    English enum value."""
    assert report_html._category("kitchen", "ar") == "مطبخ"
    assert report_html._category("dining", "ar") == "مطعم"
    assert report_html._category("kitchen", "en") == "Kitchen"
    # every category the contract can produce is present in both tables, so adding a
    # ZoneCategory without translating it fails here rather than in a client's PDF
    for category in ZoneCategory:
        for lang in ("en", "ar"):
            assert category.value in report_html.CATEGORY_LABELS[lang], (
                f"{category.value} has no {lang} label"
            )
    # an unknown value degrades to itself rather than blanking the cell
    assert report_html._category("teleporter", "ar") == "teleporter"


def test_no_model_call_is_possible_while_agents_are_disabled(monkeypatch):
    """MEYRAKI_USE_AGENTS=off was honoured only by the pipeline, so a test calling an
    agent directly would bill a real key from .env."""
    monkeypatch.setenv("MEYRAKI_USE_AGENTS", "off")
    called = []
    monkeypatch.setattr(agents, "client", lambda: called.append(1))
    with pytest.raises(RuntimeError, match="agents are disabled"):
        agents._parse("claude-sonnet-5", 100, [], agents.WireZoneGraph)
    assert called == []


def test_truncated_model_output_says_so_instead_of_leaking_a_pydantic_error(monkeypatch):
    """Arabic reports overran a 4096-token budget; messages.parse() validates inside
    the SDK call, so the user saw "Invalid JSON: EOF while parsing at column 1982"."""
    class _Tiny(pydantic.BaseModel):
        x: int

    try:
        _Tiny.model_validate_json('{"x": ')
    except pydantic.ValidationError as exc:
        truncation = exc

    class _Messages:
        def parse(self, **kwargs):
            raise truncation

    monkeypatch.setattr(settings, "agents_enabled", lambda: True)
    monkeypatch.setattr(agents, "client", lambda: type("C", (), {"messages": _Messages()})())
    with pytest.raises(RuntimeError, match="truncated at max_tokens=4096"):
        agents._parse("claude-sonnet-5", 4096, [], agents.WireZoneGraph)


def test_a_plan_with_no_guest_facing_zone_scores_none_rather_than_zero():
    graph = ZoneGraph(
        zones=[
            _sq("kitchen", ZoneCategory.KITCHEN, 0.0, 0.0, 0.5, 1.0),
            _sq("store", ZoneCategory.STORAGE, 0.5, 0.0, 1.0, 1.0),
        ],
        adjacency=[("kitchen", "store")],
        entrances=["kitchen"],
    )
    report = FlowReport(
        track=Track.SIMULATED,
        zone_flows=[ZoneFlow(zone_id="kitchen", intensity=1.0), ZoneFlow(zone_id="store", intensity=0.4)],
        bottlenecks=["kitchen"], dead_zones=[], notes=[],
    )
    assert flow_efficiency_score(graph, report) is None


def test_guest_space_with_no_traffic_scores_zero_not_missing():
    """A distinction worth keeping: no guest-facing zones at all is a gap (None), but
    guest zones reading zero traffic is a finding the client should see."""
    graph = ZoneGraph(
        zones=[
            _sq("lobby", ZoneCategory.LOBBY, 0.0, 0.0, 0.5, 1.0),
            _sq("kitchen", ZoneCategory.KITCHEN, 0.5, 0.0, 1.0, 1.0),
        ],
        adjacency=[("lobby", "kitchen")],
        entrances=["lobby"],
    )
    report = FlowReport(
        track=Track.DATA_DRIVEN,
        zone_flows=[ZoneFlow(zone_id="lobby", intensity=0.0), ZoneFlow(zone_id="kitchen", intensity=1.0)],
        bottlenecks=["kitchen"], dead_zones=[], notes=[],
    )
    assert flow_efficiency_score(graph, report) == 0.0
