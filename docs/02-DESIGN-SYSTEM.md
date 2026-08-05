# Meyraki Insight — Visual Identity & Design System

> Supersedes the dark/violet Figma-style mockups in the source materials (founder
> directive 2026-08-05: "modern minimalist, high-class pro-level — don't get attached
> to the design you saw"). Those mocks remain useful for *screen flow* only.

## 1. Design thesis

**The precision of an architectural drawing sheet, the calm of a great hotel lobby.**
Meyraki's raw material is the floorplan — ink linework on a white sheet, measured,
annotated, unhurried. The interface borrows that language: gallery-white surfaces,
hairline rules, exact typography, one deep accent. Nothing glows, nothing gradients.
Data is the only place allowed to be hot.

Reference altitude: Linear's discipline, Stripe's clarity, Aman Hotels' restraint.

## 2. Color tokens

| Token | Hex | Role |
|---|---|---|
| `paper` | `#FBFAF7` | App background — the drawing sheet |
| `surface` | `#FFFFFF` | Cards, panels, inputs |
| `ink` | `#16130E` | Primary text, poché fills, primary buttons |
| `graphite` | `#57534A` | Secondary text, icons |
| `hairline` | `#E7E4DC` | Rules, borders, dividers (1px, never heavier) |
| `viridian` | `#1C4A3E` | THE accent: links, active states, focus rings, selected states, brand moments |
| `viridian-tint` | `#EDF2EF` | Accent wash: selected backgrounds, hover fills |
| `thermal` | `#DA4B22` | **Data only** — heat/activity in charts, bottleneck pins, alerts. Never decoration. Fails AA as small text — use `thermal-text` for words. |
| `thermal-text` | `#C04117` | Text grade of thermal (≥5.0:1 on paper/surface) — error messages, failed-state labels. |
| `thermal-ramp` | `#2C5F8A → #7FB069 → #E8C547 → #DA4B22` | Heatmap gradient (cool→hot) on plans |

Rules: one accent per view. Viridian is brand; thermal is information. If a screen
shows both, viridian recedes (text links only). Dark mode is a Phase-2 deliverable —
tokens invert onto `#141311`, same accent logic.

## 3. Typography

| Role | Face | Usage |
|---|---|---|
| Display | **Instrument Serif** (400, italic for emphasis) | Page titles, section openers, hero numbers. Large sizes only (≥28px) — it is an accessory, used with Chanel restraint. |
| UI / Body | **Instrument Sans** (400/500/600) | Everything interactive and readable. 15px base, 1.6 line height. |
| Annotation | **IBM Plex Mono** (400/500) | Eyebrows, dimension labels, metrics, table figures, timestamps, zone IDs. Uppercase + 0.08em tracking for eyebrows. Tabular numerals always. |

Scale (px): 12 (mono captions) · 13 · 15 (body) · 17 · 22 · 28 · 40 · 56.
Headings in Instrument Sans 600 up to 22px; Instrument Serif from 28px up.

## 4. The signature: dimension-line annotation

Section headers and key figures are annotated like measured drawings: a hairline rule
with terminal ticks, a small mono label riding it. In HTML:

```
├──────  PHASE 01 · FOUNDATIONS  ──────┤
```

Implementation: `.dim-line` = 1px hairline with 8px vertical end ticks; label in
12px Plex Mono uppercase, graphite. Used for: report section headers, dashboard module
headers, PDF plan headers, empty-state markers. This is the one decorative device —
everything else stays plain.

## 5. Spatial system

- 8px base grid; component padding 16/24; section rhythm 64/96.
- Max content width 1120px; reading measure 68ch.
- Corner radius **2px** (drawing sheets are square; 2px keeps inputs from feeling sharp).
  Pills/round corners are off-brand.
- Elevation: no drop shadows. Depth = hairline borders + surface-on-paper contrast.
  (Single exception: modals may use `0 8px 40px rgba(22,19,14,.10)`.)
- Density: generous. White space is the luxury cue — when in doubt, add space, not lines.

## 6. Components (canonical treatments)

- **Buttons:** primary = ink fill, paper text; hover deepens to pure black. Secondary =
  hairline border, ink text. Accent actions (rare) = viridian fill. Never gradients,
  never shadows. 2px radius, 15px/500.
- **Inputs:** surface fill, hairline border, viridian focus ring (2px outline).
- **Cards:** surface + hairline; header row with mono eyebrow; no shadow.
- **Tables/metrics:** Plex Mono tabular figures; hairline row rules only (no zebra).
- **Plan viewer:** the floorplan is always the hero — ink linework on paper; zones as
  viridian-tint fills at 40%; heatmap uses the thermal ramp at 65% opacity; bottleneck
  pins thermal.
- **Progress (pipeline):** step list with mono labels and dimension-line connectors —
  the live agent feed reads like a drawing register.
- **Empty states:** a faint plan-grid watermark + one plain sentence + one action.

## 7. Motion

Almost none. 150ms ease-out on hover/focus; one orchestrated moment only: when analysis
completes, the heatmap fades onto the plan over 600ms. `prefers-reduced-motion` respected.

## 8. Voice

Plain, exact, unhurried. Sentence case everywhere. Buttons say what happens ("Generate
insights", "Download report"). Numbers carry their assumptions ("+15% flow — based on
simulated footfall, calibrated to boutique-hotel presets"). Arabic (RTL) is a first-class
citizen: mirrored layouts, Plex Mono replaced by tabular Latin numerals inside Arabic
text, IBM Plex Sans Arabic as the Arabic UI face paired with a high-contrast Arabic
display face (evaluate: Aref Ruqaa vs Amiri for display; default UI: IBM Plex Sans Arabic).

## 9. Accessibility floor

WCAG AA minimum: ink on paper = 15.9:1; graphite on paper = 7.1:1; viridian on paper =
9.2:1 — all pass. Focus visible always (viridian ring). Hit targets ≥ 40px. Heatmap
ramp readable for deuteranopia (blue→red anchors, green mid de-emphasized in legends;
values always labeled in mono).
