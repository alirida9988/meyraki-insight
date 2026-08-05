# Meyraki Insight — Tech Stack Decisions

> The original documents (2024-era) recommended Flutter-or-React, FastAPI,
> OpenCV + scikit-learn, PostgreSQL, S3. Per the founder's directive, these are MY
> recommendations with reasoning — kept where they were right, replaced where the
> 2026 landscape offers something strictly better. Evidence for AI-layer choices is in
> `04-REUSE-MAP.md`.

## Decision table

| Layer | Original docs said | Decision | Why |
|---|---|---|---|
| Frontend | Flutter Web *or* React | **Next.js (React + TypeScript) + Tailwind + shadcn/ui** | This is a web SaaS with reports, SEO pages, and heavy canvas/graphics work. React's ecosystem owns that space: Konva/Three.js for plan overlays, react-three-fiber for 3D previews, first-class PDF/report tooling, i18n/RTL solved (next-intl), and the largest hiring pool in MENA/EU. Flutter Web still renders to canvas — poor text selection, SEO, accessibility, and third-party JS interop. Flutter only wins if a native mobile app were the core product; it is not. |
| Plan viewer | (not specified) | **Konva (2D overlays) now; react-three-fiber (3D) Phase 2** | MVP value is heatmap + zone overlays on a 2D plan — a 2D canvas problem. Three.js from day one adds cost without insight. The mocks are 2D. |
| Backend | Python FastAPI | **Keep FastAPI (Python 3.12+, uv, Pydantic v2)** | The docs were right. Python is where every AI SDK, CV library, simulation tool, and reusable research repo lives; FastAPI gives async, typed contracts (Pydantic = same schemas that validate agent outputs), OpenAPI for the white-label API story. No reason to churn. |
| AI engine | OpenCV + scikit-learn | **LLM multimodal agent pipeline + deterministic CV tools** (see `01-ARCHITECTURE.md`) | 2024's plan required training custom models with data we don't have. 2026 frontier vision models read floorplans zero-shot with semantic understanding no scikit-learn pipeline could reach. OpenCV stays as *tools inside* the pipeline (masks, geometry, heatmap rendering) — deterministic, testable, free. Provider choice: §AI below. |
| Job orchestration | (not specified) | **Postgres-backed job queue (e.g. `pgqueuer`/custom) + resumable step pipeline; no Celery/Redis/Temporal at MVP** | One DB = fewer moving parts; jobs, steps, and audit rows live beside product data transactionally. Celery+Redis adds two infra pieces for zero MVP benefit; Temporal is the right upgrade only when we run thousands of concurrent long pipelines. `ponytail:` Postgres queue ceiling ~dozens of concurrent jobs — swap to Temporal/SQS at Phase 3 scale. |
| Database | PostgreSQL | **Keep PostgreSQL 16; JSONB for agent artifacts; PostGIS only when spatial SQL is actually needed** | Docs were right. Zone graphs/proposals are JSONB documents; PostGIS adds ops complexity for queries we don't run yet. |
| Storage | AWS S3 | **Keep S3-compatible object storage** (S3 or Cloudflare R2) | Right call; R2 kills egress fees for image-heavy workloads, and S3 API compatibility keeps us portable. Bedrock/AWS-alignment may tip this to S3 proper — finalize with provider choice. |
| Auth | (not specified) | **Managed auth (Clerk or Auth0), org/multi-tenant from day one** | White-label + client portals are on the roadmap; retrofitting multi-tenancy is the classic startup regret. Building auth ourselves fails the "no bugs" bar for zero differentiation. |
| PDF reports | "PDF export engine" | **Branded HTML/CSS template + Playwright `page.pdf()`** | The report reuses the web app's design system verbatim — one brand implementation, print-quality output, trivially embeds heatmaps/moodboards/charts. WeasyPrint is the Python-native fallback if we drop Node from the report path. (Research-verified; see 04-REUSE-MAP §4.) |
| Palettes | (not specified) | **node-vibrant** (MIT, maintained) | Semantic swatches (Vibrant/Muted/…) map directly to moodboard palette output. |
| Image generation | (not specified) | **Gemini 2.5 Flash Image (~$0.04/img) primary; Flux Kontext via API for photoreal restyles; both behind one internal interface** | Best quality-per-dollar for interior renders + native image-editing (restyle an existing room photo) which Imagen/DALL-E lack; Flux dev weights are non-commercial so API-only. Provider-abstracted so we can swap without touching the pipeline. (Research-verified; see 04-REUSE-MAP §2.) |
| i18n | (not specified) | **next-intl with EN + AR (RTL) from the first screen** | Arabic support is a stated competitive moat in the GCC; retrofitting RTL into a finished dark-themed UI is painful and bug-prone. |
| Hosting | (not specified) | **MVP: Vercel (front) + one container host (Railway/Fly/ECS) for API+workers. Region: EU or Bahrain per data-residency needs** | Boring, cheap, fast to ship. Decide AWS-full (me-south-1 Bahrain) only if Bedrock/data-residency demands it — see AI provider section. |

## AI provider & orchestration framework (research-verified Aug 2026)

**Models — per pipeline step (prices per 1M in/out tokens):**

| Step | Model | Why |
|---|---|---|
| Intake, Routing | **Claude Haiku 4.5** ($1/$5) | Cheap triage/classification; multimodal-in |
| Zone Analyst, Flow narrative | **Claude Sonnet 5** ($3/$15) | High-resolution vision tier (2576px long edge, pixel-accurate coordinates) — explicitly strong on architectural drawings; native strict-JSON structured outputs |
| Layout Optimizer | **Claude Opus 5** ($5/$25) | Strongest available reasoning-per-dollar for the step that IS the product |
| Report Writer (EN/AR) | Sonnet 5 | Quality Arabic prose + brand voice |
| QA Verifier | Sonnet 5 (separate instance, skeptic prompt) | Independent check, mid-tier cost |
| Moodboard images | **Gemini 2.5 Flash Image** (~$0.039/img); Flux Kontext via API for photoreal restyles | Claude doesn't generate images; Gemini is best quality-per-dollar for interior renders and supports image *editing* (restyle an existing room) |

Value alternatives (behind the same interface): GPT-5.6 Terra ($2/$12) and Gemini 3 Pro
($2/$12) both read floorplans well — kept as failover, benchmarked on our golden set.

**Provider strategy:**
- All model calls go through one thin `ModelClient` abstraction (Pydantic AI gives this
  for free) → provider swap/failover is config, not code.
- **GCC/enterprise data path (verified):** Claude Opus 5/Sonnet 5 are available on AWS
  Bedrock in `me-central-1` (UAE) and `me-south-1` (Bahrain) via global cross-region
  inference; EU in-region on `eu-west-1`/`eu-central-1`. Caveat for ministry-grade
  clients: "global cross-region" may route inference outside the Gulf — strict
  in-country inference needs contractual review; EU residency is the stronger
  compliance story today. Same prompts, different endpoint — no code changes.

**Orchestration: Pydantic AI agents + our Postgres-backed resumable job pipeline.**
- Why: our whole design is "typed step: call model → get validated schema back" — that
  is Pydantic AI's core primitive (MIT, active, provider-neutral incl. Bedrock). It
  reuses the exact Pydantic contracts our FastAPI already speaks; zero new schema
  language. Durability lives in our job queue (each step persisted + resumable), which
  we need anyway for progress streaming and audit.
- Considered and rejected for now:
  - **LangGraph** (5/5-capable, MIT): the strong alternative — adopt it if the pipeline
    grows real branching/cycles; its Platform/managed tier is Elastic-licensed.
  - **CrewAI / AG2** (autonomous crews / chat-between-agents): wrong shape for a
    deterministic document pipeline. **AutoGen**: maintenance mode since Oct 2025 — do
    not build on it.
  - **Temporal**: the correct durability upgrade at Phase 3 scale (thousands of
    concurrent long jobs), officially integrated with Pydantic AI — our step design
    ports directly.
- Cost guard: per-analysis budget enforced by the Routing Agent; Sonnet-5 intro pricing
  ($2/$10) runs through 2026-08-31 — negligible either way at MVP volume.

**Self-hosted escape hatch (Phase 3 cost lever):** Qwen3-VL (Apache 2.0) or InternVL 3.5
(MIT) on vLLM for high-volume zone/OCR extraction once volume passes ~10k analyses/month;
frontier models stay on routing/optimization/report steps.

## What we are deliberately NOT doing (and when to revisit)

- **No custom-trained CV models** until the golden-set benchmark proves frontier vision
  models insufficient for zone detection (revisit if zone-label accuracy < ~90% on the
  golden set — then fine-tune or adopt CubiCasa5k-class models, see 04-REUSE-MAP §1).
- **No DWG parsing at MVP** — PDF/PNG covers boutique hospitality; DWG via ezdxf/ODA in
  Phase 2.
- **No microservices** — one API service + one worker pool, modular Python packages
  inside. Split only when a component's scaling profile demands it.
- **No Kubernetes** at MVP. Containers on a PaaS.
- **No self-hosted image/vision models** — API costs at MVP volume are trivial versus
  GPU ops; revisit at ~10k analyses/month (Qwen-VL-class models, see 04-REUSE-MAP).
