# Meyraki Insight — System & AI Orchestration Architecture

> Companion to `00-SOURCE-OF-TRUTH.md`. Final stack choices with vendor rationale live in
> `03-TECH-STACK-DECISIONS.md`; reusable repos in `04-REUSE-MAP.md`.

## 1. High-level system

```
Browser (Next.js + Three.js/Konva)
   │  upload plan/CSV, pick objectives, watch progress (SSE), view results
   ▼
API (FastAPI) ──► PostgreSQL (projects, jobs, zones, insights, metrics)
   │              S3-compatible object storage (plans, heatmaps, moodboards, PDFs)
   ▼
Job queue (Postgres-backed) ──► AI Orchestrator (the agent pipeline, §2)
                                    │ calls: LLM vision/text APIs, image-gen API,
                                    │ deterministic tools (CV ops, simulation, PDF)
                                    ▼
                               Artifacts back to S3 + rows to Postgres
```

Principles:
- **The pipeline is deterministic; the intelligence is in the steps.** Agents don't
  wander — a fixed, resumable graph runs them step by step (exactly the founder's
  "agent 1 → agent 2 → track decision → fan-out" design).
- **Every agent's output is a validated, typed JSON contract.** A step that returns
  malformed output is retried with the validation error; the next step never receives
  garbage. This is the single most important bug-prevention mechanism.
- **Deterministic code wherever determinism is possible** (heatmap rendering, geometry
  math, PDF assembly, CSV parsing). LLMs only where judgment is required (semantic zone
  labeling, recommendations, narrative, style). Cheaper, faster, and testable.
- **Async jobs, never request-response generation.** Upload returns immediately; the
  pipeline emits progress events the UI streams live ("Zones detected: 8 → Simulating
  flow → Designing moodboard…") — which doubles as a demo-day wow moment.

## 2. The agent pipeline (step-by-step orchestration)

```
        ┌──────────────────┐
 (1)    │  INTAKE AGENT     │  "analyze what we have"
        └────────┬─────────┘
        ┌────────▼─────────┐
 (2)    │  ROUTING AGENT    │  "decide which track to run"
        └────────┬─────────┘
        ┌────────▼─────────┐
 (3)    │  ZONE ANALYST     │  vision: rooms → labeled zone graph
        └────────┬─────────┘
     ┌───────────┼──────────────┐          (parallel fan-out)
┌────▼─────┐ ┌───▼────────┐ ┌───▼──────────┐
│FLOW      │ │LAYOUT      │ │MOODBOARD     │   (4)
│ANALYST   │ │OPTIMIZER   │ │DESIGNER      │
└────┬─────┘ └───┬────────┘ └───┬──────────┘
     └───────────┼──────────────┘
        ┌────────▼─────────┐
 (5)    │ BUSINESS ANALYST  │  ROI/sqm, flow score, uplift forecast
        └────────┬─────────┘
        ┌────────▼─────────┐
 (6)    │ REPORT WRITER     │  narrative + PDF assembly
        └────────┬─────────┘
        ┌────────▼─────────┐
 (7)    │ QA VERIFIER       │  cross-checks everything before delivery
        └──────────────────┘
```

### Agent contracts

**(1) Intake Agent** — multimodal. Input: uploaded files + form fields.
Output `IntakeManifest`: `{plan_quality: ok|low_res|not_a_floorplan, plan_type:
raster|vector_pdf, has_scale_hint, footfall: none|valid|schema_errors[],
space_type_detected, floors_detected, warnings[]}`.
Rejects garbage early with a human-friendly reason (trust boundary — never let a selfie
enter the pipeline as a "floorplan").

**(2) Routing Agent** — text-only, cheap model. Input: manifest + user objectives.
Output `ExecutionPlan`: which track (`data_driven` if footfall CSV valid, else
`simulated`), which specialists run, model tier per step, and per-step budgets.
This is the "what track do we go to and what do we have exactly" step.

**(3) Zone Analyst** — vision model + CV tools. Detects rooms/walls/doors/furniture,
outputs `ZoneGraph`: polygons in normalized coords, semantic labels (reception, lobby,
café…), adjacency edges, walkable-space mask, estimated areas. Rendered back onto the
plan for the UI. Vision LLM proposes; deterministic geometry code validates (polygons
closed, no overlaps > ε, labels from a fixed taxonomy).

**(4a) Flow Analyst** — hybrid. Data-driven track: joins footfall CSV to zones, computes
per-zone intensity over time. Simulated track: pedestrian simulation over the ZoneGraph
walkable mask (entrance → attractors), calibrated by space type. Output `FlowReport`:
per-zone scores, bottlenecks with coordinates, idle/dead zones, peak windows + rendered
heatmap PNG (deterministic renderer, matching the blue→red-on-dark brand style).

**(4b) Layout Optimizer** — strongest reasoning model. Input: ZoneGraph + FlowReport +
objectives. Output `LayoutProposals`: 2–3 scenarios, each with concrete moves
("relocate reception desk to north wall", "convert dead corner to 6-seat banquette"),
the zones affected, predicted effect + confidence, and rationale grounded in the flow
data. Feeds A/B comparison view.

**(4c) Moodboard Designer** — text model plans the style (palette hexes, materials,
furniture era, lighting concept, style name) from objectives + space type + optional NL
brief; image-gen API renders 3–4 interior previews; deterministic code lays out the
board. Output `Moodboard`: palette[], materials[], style, image URLs.

**(5) Business Analyst** — computes what can be computed (areas from ZoneGraph, revenue
assumptions by space type, per-scenario deltas from Optimizer predictions) and has the
LLM only *explain* the numbers. Output `BusinessCase`: ROI per sqm current vs proposed,
Guest Flow Efficiency Score, payback estimate, assumptions list (always shown — no
black-box numbers in front of an investor or client).

> **Flow Efficiency Score, defined precisely** (2026-08-06): the area-weighted mean flow
> intensity across guest-facing zones, *measured relative to the busiest guest-facing
> zone*, scaled to 0–100. The relative part is load-bearing. Intensities reach this step
> normalised against the busiest zone on the whole plan, which is what the heatmap needs
> — a packed kitchen genuinely is the hottest room — but the score excludes back-of-house
> and utility zones. Mixing the two let the scale be set by a zone the score ignored, so
> re-typing one excluded room moved the client's headline number by ~19 points with every
> intensity unchanged. Excluded types live in one place, `meyraki_contracts.SCORE_EXCLUDED`
> (= `BACK_OF_HOUSE | UTILITY_ZONES`), and are named in the report's assumptions. No
> guest-facing zone at all → no score plus a register note; guest zones at zero traffic →
> a score of 0, which is a finding rather than a gap.

**(6) Report Writer** — composes the client-facing narrative in brand voice (EN/AR),
then deterministic HTML→PDF assembly with the client's name/logo.

**(7) QA Verifier** — separate model instance with a skeptic prompt + hard checks:
every zone referenced by any recommendation exists in ZoneGraph; percentages internally
consistent; no recommendation contradicts another; image files actually exist; Arabic
output actually Arabic. Fails → targeted re-run of the offending step (max 2 retries)
→ else human-review flag. Nothing unverified reaches a client.

### Failure & durability rules
- Each step is idempotent, persisted with its input hash; a crashed job resumes from the
  last completed step, never from zero.
- Per-step timeout + retry-with-feedback (validation errors are fed back to the model).
- Model-tier routing: cheap/fast models for intake/routing, frontier vision for zones,
  frontier reasoning for optimization, mid-tier for narrative. Cost scales with value.
- Full audit trail: every prompt, output, and artifact stored per job (debuggability +
  future fine-tuning dataset).

## 3. Data model (core tables)

`users` · `organizations` (multi-tenant, white-label fields) · `projects` (client name,
logo, space_type) · `uploads` (plan files, footfall files, checksums) · `jobs` (pipeline
runs: status, current_step, per-step timings/costs) · `zone_graphs` (JSONB) ·
`flow_reports` (JSONB + heatmap key) · `layout_proposals` (JSONB) · `moodboards` ·
`business_cases` · `reports` (PDF key, version) · `events` (SSE progress log).
JSONB now; promote hot fields to columns when queries demand it. PostGIS only when we
actually run spatial SQL queries — polygons-in-JSONB is enough for the MVP.

## 4. API surface (evolved from the original spec sheet)

```
POST /projects                          create project (client, space type, branding)
POST /projects/{id}/uploads             floorplan or footfall CSV (multipart)
POST /projects/{id}/analyses            start pipeline {objectives[], brief?, track?}
GET  /analyses/{id}                     status + step results so far
GET  /analyses/{id}/events              SSE progress stream
GET  /analyses/{id}/report.pdf          branded report (when ready)
POST /analyses/{id}/chat                Phase 2: co-designer refinement messages
```
Auth: managed provider (see stack doc), org-scoped API keys for white-label partners.

## 5. Frontend screens (MVP, mirroring the Figma mocks)

1. **Upload Your Project** — drag-drop plan, footfall CSV optional, space type
2. **Define Objectives** — goal checkboxes + free-text brief; "Generate Insights"
3. **Live progress** — streaming step feed with the pipeline visualized
4. **Insight Output** — plan with heatmap overlay + zone toggles, bottleneck pins,
   recommendation cards, A/B scenario tabs
5. **Moodboard** — palette, materials, generated renders
6. **Report** — preview + branded PDF download

Dark premium theme per brand mocks (near-black, violet accent, elegant serif display
face), i18n + RTL from day one.

## 6. Non-functional requirements

- **Accuracy honesty:** every AI number carries its assumptions; QA agent enforces.
- **Tenancy & data privacy:** org-scoped everything; client floorplans are confidential
  business data — private buckets, signed URLs, per-org encryption keys at Phase 3.
- **Cost ceiling:** per-analysis LLM budget enforced by the Routing Agent (target
  < $1.50/analysis at MVP pricing).
- **Latency target:** full pipeline < 3 min p50 (parallel fan-out is why).
- **Testability:** golden-set of ~20 floorplans with hand-labeled zones; CI runs the
  zone analyst against it and reports IoU/label accuracy drift on every change.
