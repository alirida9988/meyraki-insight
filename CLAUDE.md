# Meyraki Insight

AI-powered spatial-intelligence SaaS for hospitality interiors: floorplan + footfall data
in → zone detection, flow heatmaps, layout optimization, AI moodboards, ROI metrics,
branded PDF report out. MENA/GCC launch focus, Arabic/RTL is a differentiator.

**Read before any product/architecture work — these files are authoritative:**
- `docs/00-SOURCE-OF-TRUTH.md` — product definition, personas, features, phases,
  brand direction, conflict resolutions (wins over any older Downloads document)
- `docs/01-ARCHITECTURE.md` — system design + the 7-step agent pipeline
  (Intake → Routing → Zone Analyst → {Flow, Layout, Moodboard} → Business → Report → QA)
- `docs/03-TECH-STACK-DECISIONS.md` — chosen stack with rationale
- `docs/04-REUSE-MAP.md` — vetted GitHub repos/APIs to reuse instead of building
- `docs/05-BUILD-PLAN.md` — phased implementation plan

## Rules
- **Never claim "done/verified" from happy-path checks alone.** After every milestone
  or substantial change, launch independent verification agents (backend/DB, frontend/
  UX, pipeline/contracts) that actively try to break the work — kill mid-run, poison
  inputs, race conditions, spoofed uploads, UI state bugs — and fix confirmed findings
  before reporting completion. (Founder directive 2026-08-05 after a bug slipped through.)
- The product name is **Meyraki Insight** (sometimes misheard as "Mirakel").
- Agent pipeline outputs are typed JSON contracts validated at every step; never let a
  step consume unvalidated model output.
- Deterministic code for anything deterministic (geometry, heatmap rendering, PDF,
  metrics math); LLMs only for judgment (labeling, recommendations, narrative, style).
- Every AI-produced business number must surface its assumptions in the UI/report.
- i18n/RTL (English + Arabic) from day one. Visual identity: `docs/02-DESIGN-SYSTEM.md`
  (modern minimalist, gallery-white + ink + viridian #1C4A3E accent, thermal #DA4B22
  for data only, Instrument Serif/Sans + IBM Plex Mono, hairline rules, 2px radius,
  no shadows/gradients). The old dark/violet mockups are flow reference ONLY.
- Original source documents live in `~/Downloads` (Meyraki_* / MeyrakiInsight_* files,
  `meyraki-notes.txt`); extracted copies in scratchpad. They are historical — when they
  conflict with `docs/`, `docs/` wins.
