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


# Render quality provenance (2026-08-06): the free fallback is draft quality and the
# client must be told, or blurry output ships as Meyraki's finished design direction.

def _report_html(*, draft: bool, language: str) -> str:
    from meyraki_contracts import LayoutMove, LayoutProposals, Moodboard, Objective, Scenario

    graph = ZoneGraph(
        zones=[_sq("lobby", ZoneCategory.LOBBY, 0.0, 0.0, 0.5, 1.0),
               _sq("cafe", ZoneCategory.DINING, 0.5, 0.0, 1.0, 1.0)],
        adjacency=[("lobby", "cafe")], entrances=["lobby"],
    )
    report = FlowReport(
        track=Track.SIMULATED,
        zone_flows=[ZoneFlow(zone_id="lobby", intensity=1.0), ZoneFlow(zone_id="cafe", intensity=0.5)],
        bottlenecks=["lobby"], dead_zones=[], notes=[],
    )
    layout = LayoutProposals(objectives=[Objective.GUEST_FLOW], scenarios=[Scenario(
        id="a", name="Open lobby",
        moves=[LayoutMove(description="Shift the desk", zone_ids=["lobby"], rationale="sightline")],
        predicted_effects={"guest_flow": "+10% (est.)"}, confidence=0.7)])
    from tests.test_imagegen import PNG

    return report_html.build_html(
        project_name="Hotel Cleo", client_name="Cleo", space_type="hotel",
        narrative={"executive_summary": "Summary"}, graph=graph, flow=report, layout=layout,
        moodboard=Moodboard(
            style_name="Serene", palette=["#FBFAF7", "#1C4A3E", "#B08D57"],
            materials=["stone", "walnut", "linen"], renders_are_draft=draft,
            render_provider="pollinations" if draft else "gemini",
        ),
        business={"flow_efficiency_score": 71.4, "assumptions": []},
        heatmap_png=None, moodboard_pngs=[PNG], language=language,
    )


def test_draft_renders_are_captioned_as_draft_in_both_languages():
    en = _report_html(draft=True, language="en")
    assert "not final visuals" in en
    ar = _report_html(draft=True, language="ar")
    assert "مسوّدات" in ar and "ليست تصاميم نهائية" in ar


def test_final_renders_carry_no_draft_caption():
    for language in ("en", "ar"):
        html = _report_html(draft=False, language=language)
        assert "not final visuals" not in html and "مسوّدات" not in html


def test_hero_shot_zones_derive_from_the_shared_vocabulary():
    """This was the third hardcoded guest-facing list in the codebase, and it disagreed
    with the other two by omitting meeting rooms — so a coworking floor's meeting room
    never reached the moodboard prompt."""
    from app.agents import HERO_SHOT_ZONES, render_prompts

    assert ZoneCategory.MEETING in HERO_SHOT_ZONES
    assert not (HERO_SHOT_ZONES & SCORE_EXCLUDED), "back-of-house must never be a hero shot"

    graph = ZoneGraph(
        zones=[_sq("boardroom", ZoneCategory.MEETING, 0.0, 0.0, 0.5, 1.0),
               _sq("kitchen", ZoneCategory.KITCHEN, 0.5, 0.0, 1.0, 1.0)],
        adjacency=[("boardroom", "kitchen")], entrances=["boardroom"],
    )
    hero = render_prompts("Serene", ["oak"], "coworking", graph)[0]
    assert "Boardroom" in hero and "Kitchen" not in hero


def test_moodboard_step_reports_providers_resolution_range_and_every_failure(monkeypatch):
    """Covers the agents-enabled branch, which the rest of the offline suite never
    reaches — so `from . import imagegen` inside it was only exercised in production.
    Also pins the register note: a mixed batch must show its resolution RANGE, and the
    actionable provider reason ("flux: 402") must survive instead of being truncated."""
    from app import agents, imagegen, pipeline
    from meyraki_contracts import Moodboard

    graph = ZoneGraph(
        zones=[_sq("lobby", ZoneCategory.LOBBY, 0.0, 0.0, 1.0, 1.0)],
        adjacency=[], entrances=["lobby"],
    )
    emitted: list[str] = []
    monkeypatch.setattr(pipeline.settings, "agents_enabled", lambda: True)
    monkeypatch.setattr(pipeline, "_emit", lambda s, a, k, m: emitted.append(m))
    monkeypatch.setattr(agents, "run_moodboard", lambda *a, **k: Moodboard(
        style_name="Serene", palette=["#FBFAF7", "#1C4A3E", "#B08D57"], materials=["stone"]))
    monkeypatch.setattr(agents, "generate_moodboard_images", lambda *a, **k: (
        ["k1.jpg", "k2.jpg"],
        ["gemini: 429; flux: 402 Payment Required; pollinations: 429"],
        [imagegen.Render(b"", ".jpg", "flux", (1024, 1024)),
         imagegen.Render(b"", ".jpg", "pollinations", (768, 768))],
    ))

    class _Analysis:
        id = "a1"
        objectives = ["guest_flow"]
        brief = None

        class project:
            space_type = "hotel"

    class _Ctx:
        analysis = _Analysis()
        session = None
        outputs = {"zones": graph.model_dump()}

    board = pipeline.step_moodboard(_Ctx())
    assert board.render_provider == "flux, pollinations"
    assert board.renders_are_draft is True, "any draft render makes the whole set draft"
    note = emitted[0]
    assert "768–1024px" in note, "a mixed batch must not report only its best resolution"
    assert "flux: 402 Payment Required" in note, "the actionable reason must not be truncated"
    assert "pollinations: 429" in note, "the last provider's reason must survive too"
    assert "DRAFT" in note


# Retail category (2026-08-08) — added after a real hotel hold-out put a florist, a shop
# and a barber into `other`, where they got a default attraction and a default floor share.

def test_retail_is_a_real_category_everywhere_it_matters():
    """A new ZoneCategory is only real once every consumer knows about it. Adding one to
    the enum and nowhere else leaves it silently defaulted in the simulation and the
    solver — which is the state retail was already in as `other`."""
    from app import solver
    from app.agents import HERO_SHOT_ZONES
    from app.pedestrian import ATTRACTION
    from meyraki_contracts import SCORE_EXCLUDED, ZoneCategory

    assert ZoneCategory.RETAIL in ATTRACTION, "no guest attraction — the flow sim defaults it"
    assert ZoneCategory.RETAIL in solver.USABLE_SHARE, "no usable share — the solver defaults it"
    assert ZoneCategory.RETAIL not in SCORE_EXCLUDED, "guests shop; it counts toward the score"
    assert ZoneCategory.RETAIL in HERO_SHOT_ZONES, "a hotel boutique is a legitimate hero shot"
    for lang in ("en", "ar"):
        assert "retail" in report_html.CATEGORY_LABELS[lang]
    assert "retail" in agents_zones_prompt(), "the analyst cannot use a category it is not told about"


def agents_zones_prompt() -> str:
    from app.agents import ZONES_PROMPT

    return ZONES_PROMPT


def test_a_shop_is_priced_and_weighted_between_a_corridor_and_a_destination():
    """Sanity on the numbers, not just their presence: a guest browses a boutique more
    than they linger in a corridor, and less than they commit to a restaurant."""
    from app import solver
    from app.pedestrian import ATTRACTION
    from meyraki_contracts import ZoneCategory

    assert ATTRACTION[ZoneCategory.CORRIDOR] < ATTRACTION[ZoneCategory.RETAIL] < ATTRACTION[ZoneCategory.DINING]
    # densely fitted with display units, so less free floor than an open lounge
    assert solver.USABLE_SHARE[ZoneCategory.RETAIL] < solver.USABLE_SHARE[ZoneCategory.LOUNGE]
