"""Report HTML builder — self-contained, escaped, data-complete (M4)."""

from app.report_html import build_html
from meyraki_contracts import (
    FlowReport,
    LayoutMove,
    LayoutProposals,
    Moodboard,
    Objective,
    Point,
    Scenario,
    Track,
    Zone,
    ZoneCategory,
    ZoneFlow,
    ZoneGraph,
)


def _fixtures():
    graph = ZoneGraph(
        zones=[Zone(
            id="lobby", category=ZoneCategory.LOBBY, label="Lobby <b>XSS</b>",
            polygon=[Point(x=0, y=0), Point(x=1, y=0), Point(x=1, y=1)], confidence=0.9,
        )],
        entrances=["lobby"],
    )
    flow = FlowReport(track=Track.SIMULATED, zone_flows=[ZoneFlow(zone_id="lobby", intensity=1.0)])
    layout = LayoutProposals(
        objectives=[Objective.GUEST_FLOW],
        scenarios=[Scenario(
            id="a", name='Open "lobby" & more', confidence=0.7,
            moves=[LayoutMove(description="Move <script>alert(1)</script> desk",
                              zone_ids=["lobby"], rationale="r")],
            predicted_effects={"guest_flow": "+10% (est.)"},
        )],
    )
    moodboard = Moodboard(style_name="Test & Co", palette=["#AAAAAA", "#BBBBBB", "#CCCCCC"],
                          materials=["oak"], lighting_concept="Warm layers.")
    business = {
        "flow_efficiency_score": 71.5,
        "assumptions": [{"statement": "Simulated <flow>", "source": "pipeline"}],
    }
    narrative = {
        "executive_summary": "Summary & outcome.",
        "zone_findings": "Zones.",
        "flow_findings": "Flow.",
        "layout_recommendation": "Start with A.",
        "design_direction": "Direction.",
        "next_steps": ["Do one", "Do two"],
    }
    return dict(
        project_name="Proj <img src=x>", client_name="Cli&ent", space_type="hotel",
        narrative=narrative, graph=graph, flow=flow, layout=layout,
        moodboard=moodboard, business=business, heatmap_png=b"\x89PNG12345",
    )


def test_report_html_contains_all_data_and_escapes():
    html = build_html(**_fixtures())
    for needle in ("71.5", "Lobby", "Open &quot;lobby&quot; &amp; more", "#AAAAAA",
                   "Summary &amp; outcome.", "Do one", "data:image/png;base64,"):
        assert needle in html, needle
    assert "<script>" not in html
    assert "<img src=x>" not in html
    assert "<b>XSS</b>" not in html


def test_report_html_without_heatmap_or_score():
    fx = _fixtures()
    fx["heatmap_png"] = None
    fx["business"] = {"assumptions": []}
    html = build_html(**fx)
    assert "data:image/png" not in html
    assert ">—<" in html


# Review F6/F7 (2026-08-06): defensive narrative handling + bidi-safe direction

def test_report_html_survives_none_narrative_values_and_string_score():
    fx = _fixtures()
    fx["narrative"] = {"executive_summary": None, "next_steps": None}
    fx["business"] = {"flow_efficiency_score": "<script>x</script>", "assumptions": ["not-a-dict"]}
    html = build_html(**fx)
    assert "<script>" not in html
    assert 'dir="auto"' in html


def test_report_html_arabic_direction_attribute():
    fx = _fixtures()
    fx["project_name"] = "فندق كليو"
    html = build_html(**fx)
    assert '<h1 dir="auto">فندق كليو</h1>' in html
