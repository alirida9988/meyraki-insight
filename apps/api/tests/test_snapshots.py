"""Snapshot tests for the two deterministic renderers — a quality gate from
`docs/05-BUILD-PLAN.md` that had never been built.

Both of these produce something a client looks at, from pure code with no model in the
loop. That makes them exactly the things a snapshot protects: a refactor that shifts the
heatmap ramp, drops the Arabic RTL stylesheet, or stops escaping a zone label changes the
deliverable while every other test stays green.

Snapshots are committed under `tests/snapshots/`. To update one deliberately:

    MEYRAKI_UPDATE_SNAPSHOTS=1 .venv/bin/python -m pytest tests/test_snapshots.py

Read the diff before you do. A snapshot updated without looking is worse than no
snapshot, because it converts a caught regression into a recorded one.
"""

import hashlib
import io
import os
import re
from pathlib import Path

import pytest
from PIL import Image

from app import flow as flow_mod
from app import heatmap, report_html
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

SNAPSHOTS = Path(__file__).parent / "snapshots"
UPDATING = os.environ.get("MEYRAKI_UPDATE_SNAPSHOTS") == "1"


def _compare(name: str, actual: str) -> None:
    """Compare against the committed snapshot, or write it when updating."""
    SNAPSHOTS.mkdir(exist_ok=True)
    path = SNAPSHOTS / name
    if UPDATING or not path.exists():
        path.write_text(actual, encoding="utf-8")
        if not UPDATING:
            pytest.skip(f"wrote a new snapshot {name} — commit it and re-run")
        return
    expected = path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"{name} changed. If the change is intended, re-run with "
        f"MEYRAKI_UPDATE_SNAPSHOTS=1 and read the diff before committing."
    )


def _graph() -> ZoneGraph:
    def square(zid, cat, label, x0, y0, x1, y1):
        return Zone(
            id=zid, category=cat, label=label, confidence=0.9,
            polygon=[Point(x=x0, y=y0), Point(x=x1, y=y0), Point(x=x1, y=y1), Point(x=x0, y=y1)],
        )

    return ZoneGraph(
        zones=[
            square("entrance", ZoneCategory.ENTRANCE, "Entrance", 0.0, 0.7, 0.25, 1.0),
            square("lobby", ZoneCategory.LOBBY, "Lobby", 0.0, 0.0, 0.5, 0.7),
            square("cafe", ZoneCategory.DINING, "Café", 0.5, 0.0, 1.0, 0.6),
            square("shop", ZoneCategory.RETAIL, "Gift Shop", 0.5, 0.6, 1.0, 1.0),
        ],
        adjacency=[("entrance", "lobby"), ("lobby", "cafe"), ("lobby", "shop")],
        entrances=["entrance"],
    )


def _flow() -> FlowReport:
    return FlowReport(
        track=Track.SIMULATED,
        zone_flows=[
            ZoneFlow(zone_id="entrance", intensity=0.4),
            ZoneFlow(zone_id="lobby", intensity=1.0, is_bottleneck=True),
            ZoneFlow(zone_id="cafe", intensity=0.65),
            ZoneFlow(zone_id="shop", intensity=0.15, is_dead_zone=True),
        ],
        bottlenecks=["lobby"], dead_zones=["shop"], notes=["snapshot fixture"],
    )


def _plan_png() -> bytes:
    """A deterministic stand-in plan — a fixed gradient, so the snapshot pins the
    OVERLAY rather than whatever a photograph happened to contain."""
    img = Image.new("RGB", (400, 300))
    img.putdata([((x * 255) // 400, (y * 255) // 300, 200)
                 for y in range(300) for x in range(400)])
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------- heatmap

def test_heatmap_render_is_stable():
    """The heatmap is the picture the client points at. A shifted colour ramp or a
    polygon drawn a few pixels off is invisible to every other test."""
    png = heatmap.render(_plan_png(), _graph(), _flow())
    assert png is not None
    image = Image.open(io.BytesIO(png)).convert("RGB")
    assert image.size == (400, 300)

    # Hash the pixels rather than the file: PNG encoders vary by version, pixels do not.
    digest = hashlib.sha256(image.tobytes()).hexdigest()
    _compare("heatmap_pixels.sha256", digest + "\n")


def test_heatmap_ramp_endpoints_are_the_brand_colours():
    """Pinned separately from the pixel hash so a ramp change reports itself as a ramp
    change, not as an opaque hash mismatch."""
    assert heatmap.ramp_color(0.0) == (0x2C, 0x5F, 0x8A)
    assert heatmap.ramp_color(1.0) == (0xDA, 0x4B, 0x22)


# ---------------------------------------------------------------- report

def _report_html(language: str) -> str:
    layout = LayoutProposals(
        objectives=[Objective.GUEST_FLOW],
        scenarios=[Scenario(
            id="a", name="Open the lobby sightline",
            moves=[LayoutMove(description="Relocate the reception desk to the north wall",
                              zone_ids=["lobby"], rationale="Splits queueing from through-traffic.",
                              footprint_pct=8.0)],
            predicted_effects={"guest_flow": "+15% (est.)"}, confidence=0.72,
            solver_feasible=True)],
    )
    html = report_html.build_html(
        project_name="Hotel Cleo", client_name="Cleo Hospitality", space_type="hotel",
        narrative={"executive_summary": "The lobby carries every journey in the venue."},
        graph=_graph(), flow=_flow(), layout=layout,
        moodboard=Moodboard(style_name="Serene Boutique",
                            palette=["#FBFAF7", "#1C4A3E", "#B08D57"],
                            materials=["travertine", "walnut", "linen"]),
        business={"flow_efficiency_score": 71.4,
                  "assumptions": [{"statement": "Area-weighted mean across guest-facing zones.",
                                   "source": "deterministic"}]},
        heatmap_png=None, language=language,
    )
    # The report prints today's date. Normalising it is the whole reason this compares
    # HTML rather than PDF bytes: a snapshot that changes every midnight teaches everyone
    # to ignore it.
    return re.sub(r"\d{4}-\d{2}-\d{2}", "SNAPSHOT-DATE", html)


@pytest.mark.parametrize("language", ["en", "ar"])
def test_report_html_is_stable(language):
    """Pins the client-facing document: section order, the solver verdict, escaping, and
    for Arabic the RTL direction and the translated category column."""
    _compare(f"report_{language}.html", _report_html(language))


def test_arabic_report_keeps_its_direction_and_vocabulary():
    """Called out separately from the snapshot so a regression here names itself. Losing
    RTL turns the Arabic report into unreadable left-aligned text, and losing the
    category translation puts English enum values under an Arabic header."""
    html = _report_html("ar")
    assert 'dir="rtl"' in html or "direction:rtl" in html.replace(" ", "")
    assert "مطعم" in html, "the dining category must be translated"
    assert "متجر" in html, "the retail category must be translated"
    assert ">dining<" not in html and ">retail<" not in html


def test_report_escapes_a_hostile_zone_label():
    """Zone labels are read off a customer's uploaded drawing by a model — untrusted
    text that lands in a document the customer opens."""
    graph = _graph()
    graph.zones[0].label = '<script>alert("xss")</script>'
    html = report_html.build_html(
        project_name="X", client_name=None, space_type="hotel",
        narrative={"executive_summary": "s"}, graph=graph, flow=_flow(),
        layout=LayoutProposals(objectives=[Objective.GUEST_FLOW], scenarios=[Scenario(
            id="a", name="n", moves=[LayoutMove(description="d", zone_ids=["lobby"],
                                                rationale="r")],
            predicted_effects={"guest_flow": "+1%"}, confidence=0.5)]),
        moodboard=Moodboard(style_name="S", palette=["#000000", "#111111", "#222222"],
                            materials=["m"]),
        business={"flow_efficiency_score": 1.0, "assumptions": []},
        heatmap_png=None,
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
