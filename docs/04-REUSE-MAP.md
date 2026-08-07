# Meyraki Insight — Reuse Map (GitHub repos, APIs, models)

> Research verified 2026-08-05 by direct fetch of every repo/pricing page (5 parallel
> research passes over USA/EU/MENA sources). Rule of thumb applied: **MIT/Apache/BSD/ISC
> = safe to ship; LGPL = usable as isolated dependency with legal review; GPL = server-
> side possible but review; AGPL / CC-NC / no-license = do NOT put in the product.**
> Scores are reuse-fit 1–5 for our pipeline.

## 1. Floorplan ingestion & parsing

| Repo | License | Score | Use |
|---|---|---|---|
| [pdfplumber](https://github.com/jsvine/pdfplumber) ~10.6k★ | MIT | 5 | **ADOPTED** (`app/imaging.py`) — distinguishes a true CAD export from a scan pasted into a PDF (stroke counts), recorded as an intake warning. Vector-PDF plans → exact wall lines/rects with coordinates, no ML needed. First rung of ingestion: try vector extraction, only rasterize when scanned. |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) | Apache-2.0/BSD | 5 | **ADOPTED** (`app/imaging.py`) — renders PDF plans to raster so vector uploads get a heatmap. No poppler system dependency. |
| [ezdxf](https://github.com/mozman/ezdxf) ~1.4k★, very active | MIT | 5 | **Adopt (Phase 2).** Production DXF read/query; DWG via free ODA File Converter → DXF → ezdxf. Backbone of the CAD path. |
| [FloorplanTransformation](https://github.com/art-programmer/FloorplanTransformation) ~672★ | MIT | 3 | Raster→vector pipeline blueprint (junctions → integer programming → vector plan). Mine the algorithm, not the Torch7 code. |
| [TF2DeepFloorplan](https://github.com/zcemycl/TF2DeepFloorplan) ~268★ | GPL-3.0 ⚠ | 4 | Ready-to-run room-segmentation weights + Docker/Flask. Fallback/cross-check for the vision-LLM Zone Analyst; server-side GPL use viable but get legal sign-off. |
| [VecFormer](https://github.com/WesKwong/VecFormer) (NeurIPS'25) | Apache-2.0 | 3 | SOTA CAD symbol spotting for the DWG branch; must train ourselves (FloorPlanCAD data has research terms). Phase 2+. |
| [IfcOpenShell](https://github.com/IfcOpenShell/IfcOpenShell) ~2.7k★ | LGPL-3.0 ⚠ | 4 | Only serious IFC/BIM option; run as isolated process if/when BIM uploads matter. |
| [LibreDWG](https://github.com/LibreDWG/libredwg) | GPL-3.0 ⚠ | 3 | Fallback DWG decoder (sandboxed converter service) if ODA freeware terms don't suit. |

**Blocked, do not ship:** [CubiCasa5k](https://github.com/CubiCasa/CubiCasa5k) dataset/weights and anything trained on it (CC BY-NC) — benchmark reference only · [PyMuPDF](https://github.com/pymupdf/PyMuPDF) (AGPL; use pdfplumber + poppler instead) · Ultralytics YOLOv8 weights (AGPL) inside detection repos like floor-plan-object-detection.

## 2. Layout generation & optimization

| Repo | License | Score | Use |
|---|---|---|---|
| [LayoutGPT](https://github.com/weixi-feng/LayoutGPT) ~405★ | MIT | 5 | **Adopt the pattern.** Training-free LLM layout generation via CSS-style in-context exemplars — exactly our Layout Optimizer, re-pointed at Claude with hospitality exemplars. |
| [OR-Tools](https://github.com/google/or-tools) ~13.9k★, very active | Apache-2.0 | 5 | **ADOPTED** (`app/solver.py`) — CP-SAT multi-zone knapsack: the largest subset of a scenario's moves that fits every zone's usable floor at once, egress zones held clear. Sets `solver_feasible` + notes. CP-SAT validates/repairs LLM layout proposals against hard constraints (clearances, capacity, adjacency, egress). LLM proposes → solver guarantees. |
| [Holodeck](https://github.com/allenai/Holodeck) ~561★ | Apache-2.0 | 4 | Mine its LLM-constraints-then-solver furniture placement module (strip the AI2-THOR/Unity coupling). |
| [simanneal](https://github.com/perrygeo/simanneal) ~694★ | ISC | 4 | Tiny annealer for continuous furniture-position refinement where CP-SAT is too coarse. |

**Blocked (read the papers, not the code):** HouseDiffusion, House-GAN++ (research-only riders), ATISS (NVIDIA NC), DiffuScene (Sony NC), Graph2Plan (no license — but copy its interactive graph-editing UX idea), RPLAN dataset (research-only).

## 3. Guest-flow simulation & footfall

| Repo | License | Score | Use |
|---|---|---|---|
| [JuPedSim](https://github.com/PedestrianDynamics/jupedsim) — Jülich | LGPL-3.0 ⚠ | 5 | **ADOPTED** (`app/pedestrian.py`) — unmodified pip dependency, imported in-process. Zone polygons → one walkable surface → guests spawned at entrances with weighted destinations → per-zone dwell sampling. Finds funnel zones distance-decay cannot (all journeys crossing one lobby). Deterministic; degrades to `flow.distance_decay` with a visible note when the traced geometry cannot form a walkable surface. `pip install jupedsim`: walkable polygons from ZoneGraph → agents with goals → validated pedestrian dynamics; pairs with PedPy for density/flow metrics feeding our heatmaps. Keep as unmodified dependency. |
| [PySocialForce](https://github.com/yuxiang-gao/PySocialForce) ~183★ | MIT | 4 | ~2k-LOC social-force fallback we'd fully own if JuPedSim's LGPL bothers counsel. |
| [mesa](https://github.com/projectmesa/mesa) ~3.8k★ | Apache-2.0 | 3 | Only if we later model rich guest *behavior* (order → seat → dwell), not just movement. |
| [People-Counting-in-Real-Time](https://github.com/saimj7/People-Counting-in-Real-Time) ~615★ | MIT | 3 | Phase-3 IoT path reference (camera footfall). Swap its dated SSD for an Apache-licensed detector (RT-DETR) — **not** AGPL Ultralytics heatmaps, unless we buy their enterprise license. |


## 4. Moodboards, rendering, reports, viewers

| Item | License/price | Score | Use |
|---|---|---|---|
| **Gemini 2.5 Flash Image** ("nano-banana") | ~$0.039/img API | 5 | **Primary moodboard/render generator** — best quality-per-dollar for interiors + image *editing* (restyle an existing room photo). SynthID-watermarked. Requires billing enabled on the Google project (Free Tier has zero image quota). |
| **Pollinations** (`image.pollinations.ai`) | free, keyless | 2 | **DRAFT-quality fallback only** — second link in `app/imagegen.py`'s provider chain so renders never block on a billing state. It no longer serves FLUX: as of 2026-08-06 `GET /models` returns just `["sana"]`, a small fast distillation, and it caps output at ~768px whatever size we request (aspect ratio is honoured, pixel count is not). Measured output is soft, with materials unresolved — no travertine veining, no brass, no linen weave. Usable as a mood/material direction indicator, **not** a client visual, so `Moodboard.renders_are_draft` travels to the report and the PDF captions it in EN/AR. No SLA or commercial guarantees. Gemini is auto-preferred whenever it has quota. |
| **FLUX.1-Krea-dev** via [HF Inference Providers](https://router.huggingface.co) → fal-ai | $0.025/megapixel (~$0.025 per 1024² render) | 5 | **ADOPTED** (`app/imagegen.py` `_flux`, override with `MEYRAKI_FLUX_MODEL`) — the middle tier, and the client-visual tier while Gemini's billing hold stands. Chosen over FLUX.1-schnell, FLUX.1-dev and Qwen-Image by looking at the output on one identical prompt, not by benchmark: Krea is tuned specifically for photorealism and returns real travertine aggregate, book-matched walnut grain, brushed brass and linen weave with correct light falloff; schnell returns a softer approximation, dev reads CGI-warm, Qwen reads glossy-corporate and is 3x slower. 1024², ~320 KB JPEG, ~5 s. 8x schnell's price and worth it for an image that goes in front of a paying client; drop to `fal-ai/flux/schnell` ($0.003/MP) when volume matters more than fidelity. Billed from the `x-fal-billable-units` header the provider returns, so the receipt reconciles instead of assuming one image is one charge. `hf-inference` has deprecated FLUX outright (HTTP 410). ⚠ FLUX **dev/krea** weights are non-commercial; this is API-only. |
| gpt-image-1.5 (OpenAI) | ~$0.034/img medium | 4 | Fallback provider. (gpt-image-1 retires Oct 2026 — don't build on it.) |
| [sd-interior-design](https://github.com/alaradirik/sd-interior-design) | MIT | 4 | Reference recipe if we ever self-host: RealisticVision inpaint + MLSD + segmentation ControlNet preserves room geometry while restyling. Runnable on Replicate today. |
| [node-vibrant](https://github.com/Vibrant-Colors/node-vibrant) ~2.4k★ | MIT | 5 | **Adopt.** Semantic swatches (Vibrant/Muted/…) → moodboard palettes. |
| [Playwright](https://github.com/microsoft/playwright) `page.pdf()` | Apache-2.0 | 5 | **ADOPTED** (`app/pdf.py` for reports, `apps/web/e2e` for live E2E). Branded HTML report → print-quality PDF; reuses the web design system verbatim. [WeasyPrint](https://github.com/Kozea/WeasyPrint) (BSD) = Python-native fallback. |
| [react-planner](https://github.com/cvdlab/react-planner) ~1.5k★ | MIT ⚠ stale | 3 | Best starting point for the Phase-3 interactive layout editor (budget modernization). For view-only previews, plain Konva/react-three-fiber over our ZoneGraph JSON is less work. |
| [blueprint3d](https://github.com/furnishup/blueprint3d) ~2k★ | MIT, abandoned | 2 | Interaction-model reference only (2D draw → 3D walkthrough). |

**Blocked:** HomeDiffusion (GPL-3), DesignGenie (no license).

## 5. Agent orchestration frameworks (verified Aug 2026)

| Framework | License | Fit | Verdict |
|---|---|---|---|
| [Pydantic AI](https://github.com/pydantic/pydantic-ai) ~17k★ | MIT | 5 | **Adopt.** Typed validated outputs as the core primitive = our step contracts; provider-neutral (Anthropic/OpenAI/Gemini/Bedrock); minimal framework tax; official Temporal path when we need heavier durability. |
| [LangGraph](https://github.com/langchain-ai/langgraph) ~31k★ | MIT (Platform = Elastic ⚠) | 5 | Strong alternative; adopt if the pipeline grows real branching/cycles. |
| [Temporal](https://github.com/temporalio/temporal) ~15k★ | MIT | 5* | Phase-3 durability engine for thousands of concurrent long jobs. |
| [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) ~26k★ | MIT | 4 | Clean, but handoff-loop-shaped rather than fixed-DAG-shaped. |
| [Mastra](https://github.com/mastra-ai/mastra) ~22k★ | Apache-2.0 | 4 | Best TS option — relevant only if we moved the pipeline into Node. |
| CrewAI / AG2 / MS Agent Framework / Google ADK | MIT/Apache | 2–3 | Autonomous-crew or Azure/Gemini-leaning shapes we don't need. |
| **AutoGen** | MIT | 1 | **Maintenance mode since Oct 2025 — do not start on it.** |
| Claude Agent SDK | MIT | 2 | Superb for autonomous coding agents; Claude-only + loop-shaped — wrong fit for this pipeline (we use Claude *models* via Pydantic AI instead). |

## 6. LLMs for the pipeline (verified pricing, Aug 2026)

| Model | $/1M in/out | Role |
|---|---|---|
| Claude Haiku 4.5 | $1/$5 | Intake, Routing |
| **Claude Sonnet 5** | $3/$15 (intro $2/$10 → 2026-08-31) | Zone Analyst (high-res 2576px vision tier, pixel-accurate coordinates — built for architectural drawings), Report Writer (strong Arabic), QA Verifier |
| **Claude Opus 5** | $5/$25 | Layout Optimizer — the step that is the product |
| GPT-5.6 Terra / Gemini 3 Pro | $2/$12 | Failover/benchmark alternates behind the same interface |
| Qwen3-VL (Apache) / InternVL 3.5 (MIT) | self-host | Phase-3 cost lever at >10k analyses/mo (vLLM) |

Regional: Claude Opus 5/Sonnet 5 on **AWS Bedrock UAE (`me-central-1`) & Bahrain
(`me-south-1`)** via global cross-region inference (⚠ may route outside Gulf — not
strict in-country residency); **EU in-region** on eu-west-1/eu-central-1 = strongest
compliance story. OpenAI offers EU + UAE data-residency options. Same prompts either way.

## 7. Category blueprint worth studying

[Archilyse](https://github.com/Archilyse/Archilyse) + ArchilyseAuto (AGPL ⚠): an entire
commercial floorplan-intelligence platform open-sourced — annotation editor, digitization
pipeline, spatial simulations (sunlight/noise/connectivity). **Study the architecture,
never vendor the code** (AGPL). Closest existing analog to our category.

## 8. Full applications worth mining (from the lookalike sweep)

| Repo | License | Score | Use |
|---|---|---|---|
| [warp](https://github.com/sebo-b/warp) ~172★, active | MIT | 4 | **Mine.** Floor-map seat management: upload plan image, place/edit elements visually, zones, reports, SAML/OIDC — directly liftable floor-map interaction + auth patterns for our annotation UI. |
| [ThatOpen engine_components](https://github.com/ThatOpen/engine_components) ~692★, active | MIT | 4 | Browser BIM/IFC loading, floorplan navigation, DXF export — our Phase-2 path to CAD/BIM uploads in the web UI. |
| [blueprint3d-modern](https://github.com/charmlinn/blueprint3d-modern) ~131★, active | MIT | 3 | Maintained TypeScript rewrite of blueprint3d — better editor starting point than the originals in §4. |
| [arcada](https://github.com/mehanix/arcada) ~388★, active | Apache-2.0 | 3 | Pixi.js canvas patterns for drawing zones over uploaded plans. |
| [depthmapX](https://github.com/SpaceGroupUCL/depthmapX) ~244★, active | GPL-3.0 ⚠ | 3 | Space-syntax science (visibility graphs, isovists — "which zones see footfall") as an **isolated CLI process** feeding the Flow Analyst; never linked in. |
| [odoo pos_restaurant](https://github.com/odoo/odoo) | LGPL-3 | 3 | Best open data model for tables/floors/occupancy — study the schema. |
| [seatsurfing](https://github.com/seatsurfing/seatsurfing) ~303★ | GPL-3.0 ⚠ | 3 | Architectural blueprint for "multi-tenant SaaS around a floorplan" (Go+React+Postgres); blueprint only, no code lift. |

Also checked, not fruitful: no substantial open-source retail-footfall *platform* exists
(only CV counting ingredients — that layer is ours to build and own); NASA/Matterport
digital-twin repos irrelevant or dead; xeokit is AGPL (skip unless licensed); QloApps
(OSL-3.0 network copyleft) and openMAINT (AGPL) are feature references only.

## 9. Commercial landscape (verified Aug 2026)

**Global:** Autodesk Forma (~$185/mo; urban/site scale — nothing for interiors) ·
TestFit (buildings not interiors, no behavioral data) · Archistar (parcels/permits) ·
**Qbiq** ($16M Series A 2026; AI office test-fits in <24h — closest competitor in
spirit, but office-leasing only: no hospitality, no footfall loop, no post-occupancy
ROI) · Swapp (construction docs) · Maket ($30–1,200/mo; residential only) · Finch3D
(architect tool, no business metrics) · Placer.ai (outside-in location data — never
sees inside the plan) · RetailNext, V-Count (sensors that count traffic but never
redesign the layout in response).

**MENA:** Prop-AI (UAE, valuation) · Byit (Egypt/UAE, transactional) · Designhubz
(AR furniture commerce — acquired 2024, seat empty) · Letswork (UAE/KSA — monetizes
underused hotel/café space; not a competitor, a **partner/customer signal**). MENA
proptech raised ~$1B in 2025; **no funded MENA startup does AI spatial intelligence
for commercial/hospitality interiors** — first-mover position is open.

⚠ Correction to the old SWOT decks: "LayoutAI (KSA/UAE)" could not be verified as a
real Gulf startup — treat as unconfirmed and drop from decks.

**Meyraki's wedge (research-confirmed):** nobody closes the loop
*footfall data → layout redesign → ROI forecast → client-ready branded report* for
hospitality interiors. Analytics vendors stop at dashboards; AI-layout vendors have no
behavioral data and don't serve hospitality. The moodboard + Arabic/GCC localization
layers widen the moat.
