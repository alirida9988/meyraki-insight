# Meyraki Insight — Build Plan

> Phases are sequential milestones, each ending in something demoable. Repo/API picks
> referenced here are justified in `04-REUSE-MAP.md`; stack rationale in
> `03-TECH-STACK-DECISIONS.md`. Estimates assume 1–2 engineers + AI-assisted development.

## Milestone 0 — Foundations (week 1)
- Monorepo: `apps/web` (Next.js + TS + Tailwind + shadcn/ui), `apps/api` (FastAPI, uv),
  `packages/contracts` (Pydantic + generated TS types — ONE schema source for agent
  contracts, API, and UI).
- Postgres + object storage + managed auth wired; CI (lint, typecheck, tests) from day 1.
- Dark premium theme tokens + EN/AR i18n scaffold (RTL verified on the first screen).
- **Exit:** deployed skeleton, login, empty project dashboard, health checks green.

## Milestone 1 — Ingestion & projects (week 2)
- Projects CRUD (client name, logo, space type); floorplan upload (PNG/JPG/PDF→raster,
  validation at the trust boundary); footfall CSV upload with schema errors surfaced
  inline (`zone_name, timestamp, traffic_count`); demo presets (Cleo-style sample plan +
  the sample CSV) so every demo works without client data.
- Job pipeline skeleton: Postgres-backed queue, resumable typed steps, SSE progress
  endpoint, per-step audit rows (prompt, output, cost, duration).
- **Exit:** upload → job runs a stub pipeline → live progress streams in the UI.

## Milestone 2 — The intelligence core (weeks 3–5) ← the moat
Implement the 7-step agent pipeline (`01-ARCHITECTURE.md` §2) in order:
1. **Intake Agent** — manifest + garbage rejection (vision model).
2. **Routing Agent** — track choice + step budgets (cheap model).
3. **Zone Analyst** — vision → ZoneGraph (polygons, labels, adjacency, walkable mask);
   deterministic geometry validation; overlay renderer for the UI.
   Build the **golden set** (≈20 hand-labeled plans) FIRST; CI scores zone accuracy on it.
4. **Flow Analyst** — data-driven track (CSV join → per-zone intensity) and simulated
   track: **JuPedSim** (validated pedestrian dynamics, pip dep) or **PySocialForce**
   (MIT, fully ownable) over the walkable mask; deterministic brand-style heatmap
   renderer. PDF ingestion via **pdfplumber** (vector) / poppler rasterization (scans).
5. **Layout Optimizer** — LLM proposals in the **LayoutGPT pattern** (in-context
   hospitality exemplars, Claude Opus 5) validated/repaired by **OR-Tools CP-SAT**
   (clearances, capacity, egress as hard constraints): the model proposes, the solver
   guarantees. 2–3 scenarios with concrete moves, predicted effects, rationale.
6. **Business Analyst** — deterministic metric math + LLM explanation; assumptions
   always attached.
7. **QA Verifier** — hard cross-checks + skeptic-model pass; targeted step retries.
- **Exit:** real floorplan in → verified zones, heatmap, scenarios, metrics out; golden-set
  accuracy report in CI.

## Milestone 3 — Moodboards & the visual wow (week 6)
- Moodboard Designer: style plan (palette/materials/furniture/lighting) → image-gen API
  renders (3–4 per board) → node-vibrant palette extraction → composed board UI.
- Insight Output screen finalized: heatmap overlay + zone toggles + bottleneck pins +
  A/B scenario tabs (matching the Figma mocks' dark/violet language).
- **Exit:** the four mock screens (Upload → Objectives → Insight Output → AI-Generated
  Design) exist for real.

## Milestone 4 — The report & the business loop (week 7)
- Branded HTML report template (client logo/name, EN or AR) → Playwright PDF.
- Report versioning per analysis; download from dashboard.
- Billing scaffold: Stripe (subscription for studios, one-off per-report checkout).
- **Exit:** end-to-end: upload → analyze → moodboard → **client-ready branded PDF** →
  paid. This is the complete MVP loop from the decks.

## Milestone 5 — Hardening & pilot (week 8)
- Load/failure drills: kill workers mid-run (must resume), poison inputs (must reject
  politely), provider outage (must fail over), cost ceilings enforced.
- Security pass: signed URLs, org isolation tests, upload sanitization, rate limits.
- Run 3–5 real pilot projects (Cleo-class); collect founder feedback; tune prompts
  against the golden set.
- **Exit:** pilot-ready product + demo script matching the StartupDen narrative.

## Phase 2 backlog (post-MVP, from source docs)
Interactive AI co-designer chat · A/B what-if editing · DWG ingestion (ezdxf/ODA) ·
SketchUp/AutoCAD/Figma export · sentiment/emotional zoning · Layout Sentiment Score ·
client portal accounts · white-label theming · multi-property dashboards · IoT footfall
ingestion.

## Quality gates (the "no bugs" contract)
- Typed, validated contracts at every step boundary (Pydantic ↔ generated TS).
- Golden-set regression in CI for the Zone Analyst; snapshot tests for heatmap renderer
  and report PDF; contract tests for every agent prompt (schema-valid on 3 seeds).
- QA Verifier step blocks delivery of internally inconsistent results.
- Full audit trail per job → any bad output is reproducible and diagnosable.
- Staging environment runs the full pipeline nightly on the golden set; cost + accuracy
  drift alarms.
