# Meyraki Insight

AI spatial intelligence for hospitality interiors — floorplan + footfall in; zones,
heatmaps, layout scenarios, moodboards, ROI metrics, and a branded report out.

- **Plan & decisions:** `docs/` (00-SOURCE-OF-TRUTH → 05-BUILD-PLAN) and
  `docs/Meyraki_Insight_Build_Plan.pdf`
- **Agent instructions:** `CLAUDE.md`

## Layout

| Path | What |
|---|---|
| `apps/web` | Next.js 16 + Tailwind 4 — design tokens per `docs/02-DESIGN-SYSTEM.md` |
| `apps/api` | FastAPI — `/health`, `/contracts` (JSON Schemas for TS generation) |
| `packages/contracts` | Pydantic models for every agent-pipeline step (the typed backbone) |

## Run

```bash
# web
cd apps/web && pnpm install && pnpm dev

# api
cd apps/api && python3 -m venv .venv && .venv/bin/pip install -e ../../packages/contracts -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload

# tests
apps/api/.venv/bin/python -m pytest packages/contracts/tests
```
