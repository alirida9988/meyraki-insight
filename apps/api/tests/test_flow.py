"""Flow Analyst + heatmap renderer — deterministic intelligence tests (M2)."""

import io

from PIL import Image

from app import flow, heatmap
from meyraki_contracts import Point, Track, Zone, ZoneCategory, ZoneGraph


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
        ],
        adjacency=[("entrance", "lobby"), ("lobby", "cafe")],
        entrances=["entrance"],
    )


def test_data_driven_joins_accents_and_flags_unmatched():
    csv = (
        "zone_name,timestamp,traffic_count\n"
        "Lobby,2025-04-20 08:00,120\n"
        "Cafe,2025-04-20 08:00,20\n"       # matches "Café" via accent normalization
        "Rooftop,2025-04-20 08:00,50\n"     # not on the plan → noted, not dropped silently
    ).encode()
    report = flow.data_driven(_graph(), csv)
    by_zone = {f.zone_id: f for f in report.zone_flows}
    assert by_zone["lobby"].intensity == 1.0 and by_zone["lobby"].is_bottleneck
    assert by_zone["cafe"].intensity == round(20 / 120, 3) and by_zone["cafe"].is_dead_zone
    assert report.track == Track.DATA_DRIVEN
    assert any("rooftop" in n for n in report.notes)


def test_distance_decay_falls_off_from_the_entrance():
    report = flow.distance_decay(_graph())
    by_zone = {f.zone_id: f.intensity for f in report.zone_flows}
    assert by_zone["entrance"] == 1.0
    assert by_zone["lobby"] == 0.75
    assert by_zone["cafe"] == 0.5
    assert report.track == Track.SIMULATED


def test_heatmap_renders_png_over_plan():
    plan = io.BytesIO()
    Image.new("RGB", (400, 300), (250, 250, 247)).save(plan, format="PNG")
    report = flow.simulated(_graph())
    png = heatmap.render(plan.getvalue(), _graph(), report)
    assert png is not None
    img = Image.open(io.BytesIO(png))
    assert img.size == (400, 300)
    # the overlay must actually change pixels vs the blank plan
    assert img.convert("RGB").getpixel((100, 100)) != (250, 250, 247)


def test_heatmap_returns_none_for_unreadable_bytes():
    assert heatmap.render(b"%PDF-1.4 not a raster", _graph(), flow.simulated(_graph())) is None


def test_ramp_endpoints_match_brand():
    assert heatmap.ramp_color(0.0) == (0x2C, 0x5F, 0x8A)
    assert heatmap.ramp_color(1.0) == (0xDA, 0x4B, 0x22)
