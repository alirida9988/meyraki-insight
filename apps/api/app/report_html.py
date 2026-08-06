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
    "&family=IBM+Plex+Sans+Arabic:wght@400;500;600&family=Amiri:ital@0;1"
    '&display=swap" rel="stylesheet">'
)

# docs/02-DESIGN-SYSTEM.md §8: IBM Plex Sans Arabic for body, Amiri as display serif.
AR_CSS = """
body{font-family:'IBM Plex Sans Arabic','Instrument Sans',system-ui,sans-serif}
h1,h2,.serif,.num{font-family:'Amiri','Instrument Serif',Georgia,serif}
.dim .lbl{letter-spacing:0}
"""

# Static section labels per language. Latin numerals stay per the design system.
LABELS = {
    "en": {
        "doc": "Méyraki Insight · Spatial Intelligence Report",
        "summary": "Executive summary",
        "score": "FLOW EFFICIENCY · 0–100",
        "zones_mapped": "ZONES MAPPED",
        "scenarios_n": "SCENARIOS",
        "s1": "01 · The space",
        "s2": "02 · Guest flow",
        "s3": "03 · Layout scenarios",
        "s4": "04 · Design direction",
        "s5": "05 · Next steps",
        "assumptions": "Assumptions",
        "zones": "zones",
        "options": "options",
        "ramp": "cool → hot",
        "th": ("Zone", "Category", "Area share", "Flow intensity"),
        "confidence": "confidence",
        "materials": "MATERIALS",
        "fits": "\u2713 FITS (SOLVER)",
        "nofit": "\u2715 EXCEEDS FLOOR AREA (SOLVER)",
    },
    "ar": {
        "doc": "ميراكي إنسايت · تقرير الذكاء المكاني",
        "summary": "الملخص التنفيذي",
        "score": "كفاءة الحركة · 0–100",
        "zones_mapped": "مناطق مرسومة",
        "scenarios_n": "سيناريوهات",
        "s1": "01 · المساحة",
        "s2": "02 · حركة الضيوف",
        "s3": "03 · سيناريوهات التخطيط",
        "s4": "04 · الاتجاه التصميمي",
        "s5": "05 · الخطوات التالية",
        "assumptions": "الافتراضات",
        "zones": "منطقة",
        "options": "خيارات",
        "ramp": "بارد → ساخن",
        "th": ("المنطقة", "الفئة", "نسبة المساحة", "كثافة الحركة"),
        "confidence": "الثقة",
        "materials": "الخامات",
        "fits": "\u2713 ملائم (المحلّل)",
        "nofit": "\u2715 يتجاوز المساحة (المحلّل)",
    },
}


# Zone categories are shown to the client, so they are translated like every other
# label. Keys are ZoneCategory values; a category missing here falls back to its raw
# value rather than blanking the cell.
CATEGORY_LABELS = {
    "en": {
        "entrance": "Entrance", "reception": "Reception", "lobby": "Lobby",
        "lounge": "Lounge", "dining": "Dining", "bar": "Bar", "kitchen": "Kitchen",
        "corridor": "Corridor", "stairs": "Stairs", "elevator": "Elevator",
        "restroom": "Restroom", "terrace": "Terrace", "workspace": "Workspace",
        "meeting": "Meeting room", "storage": "Storage", "service": "Service",
        "other": "Unclassified",
    },
    "ar": {
        "entrance": "مدخل", "reception": "استقبال", "lobby": "ردهة",
        "lounge": "صالة جلوس", "dining": "مطعم", "bar": "بار", "kitchen": "مطبخ",
        "corridor": "ممر", "stairs": "سلالم", "elevator": "مصعد",
        "restroom": "دورة مياه", "terrace": "شرفة", "workspace": "مساحة عمل",
        "meeting": "قاعة اجتماعات", "storage": "مخزن", "service": "خدمات",
        "other": "غير مصنّفة",
    },
}


def _category(value: str, lang: str) -> str:
    return CATEGORY_LABELS.get(lang, CATEGORY_LABELS["en"]).get(value, value)


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
    language: str = "en",
) -> str:
    e = escape
    L = LABELS.get(language, LABELS["en"])
    rtl = language == "ar"
    # Defensive over plain-dict inputs (review F6): None values never crash escape().
    n = {k: (v if isinstance(v, str) else "") for k, v in (narrative or {}).items()}
    next_steps = [s for s in (narrative or {}).get("next_steps") or [] if isinstance(s, str)]
    intensity = {f.zone_id: f.intensity for f in flow.zone_flows}
    today = date.today().isoformat()
    raw_score = business.get("flow_efficiency_score")
    score = e(str(raw_score)) if raw_score is not None else "—"

    zone_rows = "".join(
        f"<tr><td>{e(z.label)}</td><td>{e(_category(z.category.value, language))}</td>"
        f"<td>{round(polygon_area(z.polygon) * 100, 1)}%</td>"
        f"<td>{intensity.get(z.id, 0.0)}</td></tr>"
        for z in graph.zones
    )

    scenario_cards = "".join(
        '<div class="card">'
        f'<p style="font-weight:600">{e(s.name)} '
        f'<span class="mono muted" style="font-size:7.5pt">· {e(L["confidence"])} {round(s.confidence * 100)}%</span>'
        f'<span class="mono" style="font-size:7pt;margin-left:6px;color:'
        + ("var(--viridian)" if s.solver_feasible else "var(--thermal-text)")
        + '">'
        + (e(L["fits"]) if s.solver_feasible else e(L["nofit"]))
        + "</span></p>"
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
        from .imagegen import media_type

        cells = "".join(
            '<img style="width:32%;aspect-ratio:1;object-fit:cover;border:1px solid '
            'var(--hairline);border-radius:2px" src="data:'
            + media_type(png)  # png or jpeg, from magic bytes — never assumed
            + ";base64,"
            + base64.b64encode(png).decode()
            + '" alt="Interior render">'
            for png in moodboard_pngs[:3]
        )
        renders_html = (
            '<div style="display:flex;gap:2%;margin-top:5mm">' + cells + "</div>"
        )

    assumptions = "".join(
        f'<p style="font-size:8.5pt" class="muted">· {e(a.get("statement", ""))}</p>'
        for a in business.get("assumptions", []) if isinstance(a, dict)
    )

    steps = "".join(
        f'<p style="font-size:9.5pt;margin:3px 0"><span class="mono" style="color:var(--viridian)">'
        f"{i + 1:02d}</span>&nbsp; {e(s)}</p>"
        for i, s in enumerate(next_steps)
    )
    footer = (
        f'<div class="foot"><span>Méyraki Insight — {e(project_name)}</span>'
        f"<span>{today}</span></div>"
    )

    return f"""<!DOCTYPE html><html lang="{e(language)}" dir="{'rtl' if rtl else 'ltr'}"><head><meta charset="utf-8">
<title>Meyraki Insight — {e(project_name)}</title>{FONTS}<style>{CSS}{AR_CSS if rtl else ""}</style></head><body>

<section class="page">
  {_dim(L["doc"], today)}
  <div style="margin-top:22mm">
    <p class="mono muted" style="font-size:8pt;letter-spacing:.14em;text-transform:uppercase" dir="auto">
      {e(client_name or "Client")} · {e(space_type)}</p>
    <h1 dir="auto">{e(project_name)}</h1>
  </div>
  <div style="margin-top:14mm;max-width:150mm">
    {_dim(L["summary"])}
    <p style="font-size:11pt" dir="auto">{e(n.get("executive_summary", ""))}</p>
  </div>
  <div style="margin-top:12mm;display:flex;gap:14mm;align-items:flex-end">
    <div><p class="num">{score}</p>
      <p class="mono muted" style="font-size:7.5pt;letter-spacing:.1em">{e(L["score"])}</p></div>
    <div><p class="num">{len(graph.zones)}</p>
      <p class="mono muted" style="font-size:7.5pt;letter-spacing:.1em">{e(L["zones_mapped"])}</p></div>
    <div><p class="num">{len(layout.scenarios)}</p>
      <p class="mono muted" style="font-size:7.5pt;letter-spacing:.1em">{e(L["scenarios_n"])}</p></div>
  </div>
  {footer}
</section>

<section class="page">
  {_dim(L["s1"], f"{len(graph.zones)} {L['zones']}")}
  <p style="max-width:150mm" dir="auto">{e(n.get("zone_findings", ""))}</p>
  <table style="margin-top:6mm"><tr>{''.join(f'<th>{e(h)}</th>' for h in L['th'])}</tr>
  {zone_rows}</table>
  <div style="margin-top:8mm">{_dim(L["s2"], L["ramp"])}
  <p style="max-width:150mm" dir="auto">{e(n.get("flow_findings", ""))}</p>
  {heatmap_html}</div>
  {footer}
</section>

<section class="page">
  {_dim(L["s3"], f"{len(layout.scenarios)} {L['options']}")}
  <p style="max-width:150mm" dir="auto">{e(n.get("layout_recommendation", ""))}</p>
  <div style="margin-top:5mm">{scenario_cards}</div>
  {footer}
</section>

<section class="page">
  {_dim(L["s4"], moodboard.style_name)}
  <p style="max-width:150mm" dir="auto">{e(n.get("design_direction", ""))}</p>
  {renders_html}
  <div style="margin-top:5mm">{swatches}</div>
  <p style="margin-top:5mm;font-size:9.5pt"><span class="mono muted" style="font-size:7.5pt;
  letter-spacing:.12em">{e(L["materials"])} · </span>{e(", ".join(moodboard.materials))}</p>
  {f'<p class="muted" style="font-size:9.5pt">{e(moodboard.lighting_concept)}</p>' if moodboard.lighting_concept else ""}
  <div style="margin-top:10mm">{_dim(L["s5"])}
  {steps}</div>
  <div style="margin-top:10mm">{_dim(L["assumptions"])}
  {assumptions}</div>
  {footer}
</section>

</body></html>"""
