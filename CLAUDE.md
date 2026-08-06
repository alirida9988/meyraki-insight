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

## How we work — the verification ritual (founder directive, non-negotiable)

Nothing is "done" until it has survived every layer that applies. Run `/ship-check` (see
`.claude/skills/ship-check/`) before any completion claim.

1. **Unit — offline, deterministic.** `apps/api/.venv/bin/python -m pytest tests
   ../../packages/contracts/tests -q`. Never calls a model (`MEYRAKI_USE_AGENTS=off`
   in `tests/conftest.py`). Every confirmed bug gets a regression test here, named
   for the finding, so it can never come back silently.
2. **Live QA hunt — what a real user actually does.** `tests/qa_hunt.py` against the
   running stack with real agents: oversized 300 DPI scans, phone-photo JPEGs,
   architect PDFs, grayscale scanner output, European semicolon CSVs, UTF-16 Excel
   exports, double-clicks, refreshes. This layer has found bugs that 73 unit tests
   and 3 E2E scenarios never touched — it is not optional.
3. **Golden-set drift benchmark — the model's judgement, pinned.**
   `tests/golden_set.py` scores labelled plans in `tests/golden/` against the
   properties a correct reading must have. Run it before *and* after touching any
   prompt, model id, or the repair layer: unit tests pin code you wrote, this pins
   behaviour you don't control. A prompt edit that helps one plan and breaks another
   is invisible to every other layer. A failed check is a question — open the plan
   image and decide whether the label or the product is wrong.
4. **Playwright E2E — the real browser, the real stack.** `apps/web/e2e`. Assert what
   the user *sees*, not what the DOM contains: images must be decoded
   (`naturalWidth > 0`), PDFs must start with `%PDF-`, cross-org access must 404.
5. **Adversarial review agents.** After every milestone, launch independent reviewers
   (backend/DB, frontend/UX, security, contracts) whose brief is to *break* the work —
   kill mid-run, poison inputs, race it, spoof uploads, leak keys. Fix every confirmed
   finding, add its regression test, then re-run their own repros.

Working rules that came out of real failures:
- **Evidence before fixes.** Reproduce first; a fix without a repro is a guess.
- **Root cause, not the symptom.** Fix it where all callers route through.
- **A test that lies is worse than no test.** When a check fails, first ask whether the
  product or the fixture is wrong (synthetic box-diagrams being rejected as "not a
  floorplan" was the Intake Agent working correctly).
- **Degrade, never fail the run.** Optional enrichment (renders, heatmaps, fonts) must
  fall back with a *visible* note in the pipeline register — never silently, never fatally.
- **Check `docs/04-REUSE-MAP.md` before writing anything a vetted repo already does.**
  Recommendations rot into fiction if nobody adopts them: mark adopted rows **ADOPTED**
  with the module that uses them.
- **CI runs layer 1 only** (`.github/workflows/ci.yml`) — layers 2–5 cost model credits
  and run locally before a claim. Never let a paid call into the pytest suite.
- Report outcomes faithfully: what was checked, what was found, what is still open.

## Rules
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
- **Deliverables convention (founder directive 2026-08-06):** every generated document,
  report PDF, heatmap, render, or verification screenshot the founder should see goes
  into `artifacts/` at the repo root (gitignored, on disk for browsing).
- Original source documents live in `~/Downloads` (Meyraki_* / MeyrakiInsight_* files,
  `meyraki-notes.txt`); extracted copies in scratchpad. They are historical — when they
  conflict with `docs/`, `docs/` wins.
