---
name: ship-check
description: Run the Meyraki verification ritual before claiming any work is done — offline unit tests, the live QA hunt with real user inputs, Playwright E2E in a real browser, and adversarial review agents. Use whenever finishing a feature, fixing a bug, or about to report completion, and whenever the user says "proceed", "test everything", "no bugs", or asks whether something is really working.
---

# Ship-check — the verification ritual

Nothing in this repo is "done" until it survives all four layers. Skipping a layer
because the change "looks small" is how the bugs that reached the founder got in.

## Layer 1 — Offline unit tests (always)

```bash
cd apps/api && rm -f test_meyraki.db && \
  .venv/bin/python -m pytest tests ../../packages/contracts/tests -q
```

- Must never make a model call. `tests/conftest.py` sets `MEYRAKI_USE_AGENTS=off`
  and `MEYRAKI_RATELIMIT=off`, and resets the SQLite file before any module opens a
  connection (deleting it later makes SQLite report a readonly database).
- Every confirmed bug gets a regression test named for its finding.
- Web: `cd apps/web && pnpm lint && pnpm build`.

## Layer 2 — Live QA hunt (any change to ingestion, pipeline, or API)

```bash
cd apps/api && (.venv/bin/uvicorn app.main:app --port 8000 &) && sleep 4 && \
  .venv/bin/python tests/qa_hunt.py
```

Real agents, real network, real credits. It exercises what a customer actually does:
oversized 300 DPI scans, phone-photo JPEGs, architect PDFs, grayscale scans, semicolon
and UTF-16 CSVs, concurrent analyses, refresh recovery. Add a scenario whenever you
imagine a new way a user could hold it wrong.

**Interpreting a failure:** decide whether the *product* or the *fixture* is wrong.
Content-judged paths must use a real plan (`apps/web/e2e/fixtures/cleo_plan.png`) —
the Intake Agent correctly rejects synthetic box diagrams, and that is not a bug.

## Layer 2b — Golden-set drift benchmark (any prompt, model id, or repair-layer change)

```bash
cd apps/api && .venv/bin/python tests/golden_set.py --save
```

Labelled plans in `tests/golden/` with the properties a correct reading must have.
Exit 0 pass · 1 fail · **2 inconclusive** (the API was throttled or unreachable — not a
model regression, so never report it as one). `--save` writes
`artifacts/golden_set_report.json`.

Unit tests pin down code you wrote; this pins down behaviour you don't control. Run it
*before and after* touching a prompt or a model id — a prompt edit that improves one
plan and quietly breaks another is invisible to every other layer.

**Hard checks are not negotiable.** Intake quality, the rejection sentence, required
categories, and "the model actually classified the rooms" fail the run outright at any
score. A percentage over N checks cannot express "this must never happen": accepting a
bar chart as a floorplan was worth 8% and passed comfortably.

**A failed check is a question, not a verdict.** Open the plan image and decide whether
the label or the product is wrong. Both have happened here:
- *Product wrong:* two rooms printed "CAFÉ" came back as `other`, costing the café 5x
  guest attraction in the flow simulation and the wrong usable share in the solver. The
  fix was three lines of prompt ("classify by function, not by wording"). A keyword
  table was also written, then **reverted** — a review proved it typed a GCC VIP majlis
  as a bar (`صالة كبار` contains `بار`) and a swimming pool as a restroom. Verify which
  half of a two-part fix actually worked before keeping both.
- *Label wrong:* a coworking plan was labelled "entrance not drawn" while the model kept
  finding one, and a 0.7m unlabelled wall gap was too ambiguous to assert either way.
  The fixture was redrawn with double doors and a threshold line.

Ground truth must be true by construction — `tests/golden/make_plans.py` asserts every
caption sits inside its own room, because a hand-placed version had put KITCHEN and
STORE in the same room.

## Layer 3 — Playwright E2E (any change the user can see)

```bash
# API on :8000 and web on :3000 first
cd apps/web && npx playwright test          # add E2E_PDF=1 / E2E_AR=1 for the opt-in runs
```

Assert what the user *sees*: images decoded (`naturalWidth > 0`), PDFs starting with
`%PDF-`, cross-org access returning 404, a refresh recovering a running analysis.

## Layer 4 — Adversarial review (every milestone)

Launch independent agents (backend/DB, frontend/UX, security, contracts) briefed to
**break** the work — kill workers mid-run, race concurrent requests, spoof content
types, leak artifact keys, poison model output, exhaust quotas. Then:

1. Reproduce every finding before fixing it.
2. Fix at root cause, where all callers route through.
3. Add a regression test per finding.
4. Re-run the reviewer's own repros to confirm.

## Before reporting

- [ ] Every applicable layer run, with the numbers to quote (e.g. "100 offline, golden
      set 24/24, 4/4 live E2E").
- [ ] Deliverables copied into `artifacts/` (report PDFs, heatmaps, screenshots).
- [ ] Servers stopped; `git status` clean; commit message states what was verified.
- [ ] Anything still open is named plainly — never implied to be finished.
