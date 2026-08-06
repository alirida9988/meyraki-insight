"""Branded insight-report HTML — docs/02-DESIGN-SYSTEM.md rendered for print.

All dynamic strings are escaped; the heatmap is embedded as a data URI so the
document is self-contained (fonts degrade gracefully offline).
"""

import base64
from datetime import date
from html import escape

from meyraki_contracts import FlowReport, LayoutProposals, Moodboard, ZoneGraph

from .geometry import polygon_area

CSS = """
:root{--paper:#FBFAF7;--surface:#fff;--ink:#16130E;--graphite:#57534A;
--hairline:#E7E4DC;--viridian:#1C4A3E;--thermal-text:#C04117}
*{margin:0;padding:0;box-sizing:border-box}
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{font-family:'Instrument Sans',system-ui,sans-serif;color:var(--ink);
background:var(--paper);font-size:10.5pt;line-height:1.55}
.page{padding:16mm 15mm;page-break-after:always;background:var(--paper)}
.page:last-child{page-break-after:auto}
.serif{font-family:'Instrument Serif',Georgia,serif;font-weight:400}
.mono{font-family:'IBM Plex Mono',ui-monospace,monospace}
.dim{display:flex;align-items:center;gap:12px;margin:0 0 12px}
.dim .lbl{font-family:'IBM Plex Mono',ui-monospace,monospace;font-size:7.5pt;
letter-spacing:.14em;text-transform:uppercase;color:var(--graphite);white-space:nowrap}
.dim .ln{flex:1;height:1px;background:var(--hairline);position:relative}
.dim .ln::before,.dim .ln::after{content:"";position:absolute;top:-4px;width:1px;
height:9px;background:var(--graphite)}
.dim .ln::before{left:0}.dim .ln::after{right:0}
h1{font-family:'Instrument Serif',Georgia,serif;font-weight:400;font-size:30pt;line-height:1.1}
h2{font-family:'Instrument Serif',Georgia,serif;font-weight:400;font-size:18pt;margin:0 0 8px}
p{margin:0 0 8px}
.muted{color:var(--graphite)}
table{width:100%;border-collapse:collapse;font-size:9pt}
th{font-family:'IBM Plex Mono',ui-monospace,monospace;font-size:7pt;letter-spacing:.12em;
text-transform:uppercase;color:var(--graphite);text-align:left;font-weight:500;
padding:5px 8px 5px 0;border-bottom:1px solid var(--ink)}
td{padding:5px 8px 5px 0;border-bottom:1px solid var(--hairline)}
.card{background:var(--surface);border:1px solid var(--hairline);border-radius:2px;
padding:12px 14px;margin-bottom:8px;break-inside:avoid}
.num{font-family:'Instrument Serif',Georgia,serif;font-size:34pt;color:var(--viridian);line-height:1}
.chip{display:inline-block;border:1px solid var(--viridian);color:var(--viridian);
border-radius:2px;padding:2px 7px;font-family:'IBM Plex Mono',ui-monospace,monospace;
font-size:7.5pt;margin:2px 4px 2px 0}
.sw{display:inline-block;width:19mm;margin-right:2mm;vertical-align:top}
.sw .c{height:13mm;border:1px solid var(--hairline);border-radius:2px}
.sw .n{font-family:'IBM Plex Mono',ui-monospace,monospace;font-size:6.5pt;color:var(--graphite)}
.heatmap{width:100%;border:1px solid var(--hairline);border-radius:2px}
.foot{display:flex;justify-content:space-between;font-family:'IBM Plex Mono',ui-monospace,monospace;
font-size:6.8pt;color:var(--graphite);letter-spacing:.1em;text-transform:uppercase;
border-top:1px solid var(--hairline);padding-top:5px;margin-top:10mm}
"""

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500'
    "&family=Instrument+Sans:wght@400;500;600&family=Instrument+Serif:ital@0;1"
    '&display=swap" rel="stylesheet">'
)


def _dim(label: str, right: str = "") -> str:
    r = f'<span class="lbl">{escape(right)}</span>' if right else ""
    return f'<div class="dim"><span class="lbl">{escape(label)}</span><span class="ln"></span>{r}</div>'


def build_html(
    *,
    project_name: str,
    client_name: str | None,
    space_type: str,
    narrative: dict,
    graph: ZoneGraph,
    flow: FlowReport,
    layout: LayoutProposals,
    moodboard: Moodboard,
    business: dict,
    heatmap_png: bytes | None,
    moodboard_pngs: list[bytes] | None = None,
) -> str:
    e = escape
    intensity = {f.zone_id: f.intensity for f in flow.zone_flows}
    today = date.today().isoformat()
    score = business.get("flow_efficiency_score")

    zone_rows = "".join(
        f"<tr><td>{e(z.label)}</td><td>{e(z.category.value)}</td>"
        f"<td>{round(polygon_area(z.polygon) * 100, 1)}%</td>"
        f"<td>{intensity.get(z.id, 0.0)}</td></tr>"
        for z in graph.zones
    )

    scenario_cards = "".join(
        '<div class="card">'
        f'<p style="font-weight:600">{e(s.name)} '
        f'<span class="mono muted" style="font-size:7.5pt">· confidence {round(s.confidence * 100)}%</span></p>'
        + "".join(
            f'<p style="font-size:9pt;margin:4px 0"><b>{e(m.description)}</b> '
            f'<span class="muted">— {e(m.rationale)}</span></p>'
            for m in s.moves
        )
        + "".join(f'<span class="chip">{e(k)}: {e(v)}</span>' for k, v in s.predicted_effects.items())
        + "</div>"
        for s in layout.scenarios
    )

    swatches = "".join(
        f'<div class="sw"><div class="c" style="background:{e(c)}"></div><div class="n">{e(c)}</div></div>'
        for c in moodboard.palette
    )

    heatmap_html = ""
    if heatmap_png:
        uri = "data:image/png;base64," + base64.b64encode(heatmap_png).decode()
        heatmap_html = f'<img class="heatmap" src="{uri}" alt="Guest-flow heatmap">'

    renders_html = ""
    if moodboard_pngs:
        cells = "".join(
            '<img style="width:32%;aspect-ratio:1;object-fit:cover;border:1px solid '
            'var(--hairline);border-radius:2px" src="data:image/png;base64,'
            + base64.b64encode(png).decode()
            + '" alt="Interior render">'
            for png in moodboard_pngs[:3]
        )
        renders_html = (
            '<div style="display:flex;gap:2%;margin-top:5mm">' + cells + "</div>"
        )

    assumptions = "".join(
        f'<p style="font-size:8.5pt" class="muted">· {e(a.get("statement", ""))}</p>'
        for a in business.get("assumptions", [])
    )

    steps = "".join(
        f'<p style="font-size:9.5pt;margin:3px 0"><span class="mono" style="color:var(--viridian)">'
        f"{i + 1:02d}</span>&nbsp; {e(s)}</p>"
        for i, s in enumerate(narrative.get("next_steps", []))
    )
    footer = (
        f'<div class="foot"><span>Méyraki Insight — {e(project_name)}</span>'
        f"<span>{today}</span></div>"
    )

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Meyraki Insight — {e(project_name)}</title>{FONTS}<style>{CSS}</style></head><body>

<section class="page">
  {_dim("Méyraki Insight · Spatial Intelligence Report", today)}
  <div style="margin-top:22mm">
    <p class="mono muted" style="font-size:8pt;letter-spacing:.14em;text-transform:uppercase">
      {e(client_name or "Client")} · {e(space_type)}</p>
    <h1>{e(project_name)}</h1>
  </div>
  <div style="margin-top:14mm;max-width:150mm">
    {_dim("Executive summary")}
    <p style="font-size:11pt">{e(narrative.get("executive_summary", ""))}</p>
  </div>
  <div style="margin-top:12mm;display:flex;gap:14mm;align-items:flex-end">
    <div><p class="num">{score if score is not None else "—"}</p>
      <p class="mono muted" style="font-size:7.5pt;letter-spacing:.1em">FLOW EFFICIENCY · 0–100</p></div>
    <div><p class="num">{len(graph.zones)}</p>
      <p class="mono muted" style="font-size:7.5pt;letter-spacing:.1em">ZONES MAPPED</p></div>
    <div><p class="num">{len(layout.scenarios)}</p>
      <p class="mono muted" style="font-size:7.5pt;letter-spacing:.1em">SCENARIOS</p></div>
  </div>
  {footer}
</section>

<section class="page">
  {_dim("01 · The space", f"{len(graph.zones)} zones")}
  <p style="max-width:150mm">{e(narrative.get("zone_findings", ""))}</p>
  <table style="margin-top:6mm"><tr><th>Zone</th><th>Category</th><th>Area share</th><th>Flow intensity</th></tr>
  {zone_rows}</table>
  <div style="margin-top:8mm">{_dim("02 · Guest flow", "cool → hot")}
  <p style="max-width:150mm">{e(narrative.get("flow_findings", ""))}</p>
  {heatmap_html}</div>
  {footer}
</section>

<section class="page">
  {_dim("03 · Layout scenarios", f"{len(layout.scenarios)} options")}
  <p style="max-width:150mm">{e(narrative.get("layout_recommendation", ""))}</p>
  <div style="margin-top:5mm">{scenario_cards}</div>
  {footer}
</section>

<section class="page">
  {_dim("04 · Design direction", moodboard.style_name)}
  <p style="max-width:150mm">{e(narrative.get("design_direction", ""))}</p>
  {renders_html}
  <div style="margin-top:5mm">{swatches}</div>
  <p style="margin-top:5mm;font-size:9.5pt"><span class="mono muted" style="font-size:7.5pt;
  letter-spacing:.12em">MATERIALS · </span>{e(", ".join(moodboard.materials))}</p>
  {f'<p class="muted" style="font-size:9.5pt">{e(moodboard.lighting_concept)}</p>' if moodboard.lighting_concept else ""}
  <div style="margin-top:10mm">{_dim("05 · Next steps")}
  {steps}</div>
  <div style="margin-top:10mm">{_dim("Assumptions")}
  {assumptions}</div>
  {footer}
</section>

</body></html>"""
