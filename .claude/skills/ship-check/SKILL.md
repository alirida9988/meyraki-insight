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

- [ ] All four layers run, with the numbers to quote (e.g. "89 offline, 4/4 live E2E").
- [ ] Deliverables copied into `artifacts/` (report PDFs, heatmaps, screenshots).
- [ ] Servers stopped; `git status` clean; commit message states what was verified.
- [ ] Anything still open is named plainly — never implied to be finished.
