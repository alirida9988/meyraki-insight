"""Deterministic heatmap renderer — thermal ramp over the client's floorplan image.

Brand contract (docs/02-DESIGN-SYSTEM.md): ramp #2C5F8A → #7FB069 → #E8C547 → #DA4B22,
soft edges, the plan itself stays the hero. PNG/JPG plans only; PDF plans return None
until rasterization lands (M3, poppler).
"""

import io

from PIL import Image, ImageDraw, ImageFilter

from meyraki_contracts import FlowReport, ZoneGraph

RAMP = [(0x2C, 0x5F, 0x8A), (0x7F, 0xB0, 0x69), (0xE8, 0xC5, 0x47), (0xDA, 0x4B, 0x22)]
OVERLAY_ALPHA = 150  # ~60%


def ramp_color(intensity: float) -> tuple[int, int, int]:
    t = min(1.0, max(0.0, intensity)) * (len(RAMP) - 1)
    i = min(int(t), len(RAMP) - 2)
    frac = t - i
    a, b = RAMP[i], RAMP[i + 1]
    return tuple(round(a[c] + (b[c] - a[c]) * frac) for c in range(3))  # type: ignore[return-value]


def render(plan_bytes: bytes, graph: ZoneGraph, flow: FlowReport) -> bytes | None:
    try:
        plan = Image.open(io.BytesIO(plan_bytes)).convert("RGBA")
    except Exception:
        return None  # not a raster Pillow can read (e.g. PDF) — caller records no heatmap

    w, h = plan.size
    intensity = {f.zone_id: f.intensity for f in flow.zone_flows}
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for zone in graph.zones:
        level = intensity.get(zone.id)
        if level is None:
            continue
        color = ramp_color(level)
        pixels = [(p.x * w, p.y * h) for p in zone.polygon]
        draw.polygon(pixels, fill=(*color, OVERLAY_ALPHA))

    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=max(2, int(min(w, h) * 0.02))))
    out = Image.alpha_composite(plan, overlay)
    buf = io.BytesIO()
    out.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()
