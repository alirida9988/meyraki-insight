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
- Load/failure drills ✅ — kill workers mid-run (SIGKILL during the layout step against
  the containers; resumed and finished, and the cost receipt proves intake and zones were
  billed once, not twice), poison inputs (`tests/qa_hunt.py`), provider outage (the render
  chain fails over FLUX → Gemini → free tier, captioning the result honestly), cost
  ceilings enforced (`app/costs.py`).
- Security pass ✅ — signed URLs (`app/sharing.py`: HMAC over analysis id AND expiry, clamped TTL, constant-time compare, fails closed with no secret, and every bad link returns the same 404 as an unknown analysis so the endpoint is not an existence oracle), org isolation tests, upload sanitization, rate limits.
- Run 3–5 real pilot projects (Cleo-class); collect founder feedback; tune prompts
  against the golden set.
- **Exit:** pilot-ready product ✅ + demo script ✅ `artifacts/demo-script.md`.
  Remaining for pilot: 3–5 real client projects, which needs real plans.

## Phase 2 backlog (post-MVP, from source docs)
Interactive AI co-designer chat · A/B what-if editing · DWG ingestion (ezdxf/ODA) ·
SketchUp/AutoCAD/Figma export · sentiment/emotional zoning · Layout Sentiment Score ·
client portal accounts · white-label theming · multi-property dashboards · IoT footfall
ingestion.

## Quality gates (the "no bugs" contract)
- Typed, validated contracts at every step boundary (Pydantic ↔ generated TS).
- Golden-set regression for the Zone Analyst (`tests/golden_set.py`, opt-in — it spends
  model credits, so it runs before a claim rather than on every push); snapshot tests for
  the heatmap renderer and the report ✅ `tests/test_snapshots.py`, pinned by pixel hash
  and by HTML with the date normalised, and verified to fail on a one-digit colour change;
  contract tests for every agent prompt (schema-valid on 3 seeds) ✅
  `tests/prompt_contracts.py`, opt-in for the same reason — **15/15 seeds, $0.58**.
  It measures output-budget headroom rather than only pass/fail, because the SDK already
  enforces the schema: the real failure mode is truncation at `max_tokens`, which surfaces
  as `EOF while parsing` and names the schema instead of the budget. Seeds are the worst
  realistic case (12-zone hotel, every objective, Arabic), and ceilings are read from the
  value `_parse` is actually called with, so they cannot drift from `agents.py`.
  **Finding: an Arabic report consumes 2.1× the output tokens of the identical English
  one** (17.8% vs 8.4% of 16000). That is the measured cause of the Arabic truncation bug
  — at the old 4096 ceiling the same document sat near 70% of budget — and it makes
  "roughly double the English budget" the design rule for any Arabic-facing prompt.
  Headroom today: intake 4–6% of 2048 · zones 5–38% · layout 15–19% · moodboard 10–12%
  of 4096 · report 7–18%.
- QA Verifier step blocks delivery of internally inconsistent results ✅ — and it did
  *not* until 2026-08-09. The verifier is step 9 while the report is written in step 8, so
  the PDF exists on disk before the inconsistency is found; a failed verdict set
  `analysis.status = "failed"` and nothing consulted that status again. A studio could
  mint a client link for an analysis whose layout referenced zones the Zone Analyst never
  produced. The line is now drawn at delivery rather than access: the owner may still
  download a failed report (diagnosis, their own data), while minting a client link
  returns 409 naming the QA issue and redeeming one returns the uniform 404. Redemption is
  re-checked because an analysis clean when shared can be re-run and fail, and a link
  already in a client's inbox must go dead. Verified live over HTTP against Postgres,
  not only through the test client (`tests/test_qa_blocks_delivery.py`).
- Full audit trail per job → any bad output is **diagnosable**: every step persists its
  validated output, its error and its timings, and `cost_entries` records the model id,
  tokens and spend for each call, so you can see the exact zone graph a bad layout was
  built from. **Reproducible only within model nondeterminism** — the same plan re-run
  will not produce byte-identical output, and the raw pre-repair model response is not
  kept. Known gap: `CONTRACT_VERSION` exists but is not stamped on an analysis, so a
  report produced before a prompt change is indistinguishable from one produced after.
  Worth closing before any client disputes a delivered number.
- Staging environment runs the full pipeline nightly on the golden set; cost + accuracy
  drift alarms.
