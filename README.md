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

# tests — offline, no model calls, no network
cd apps/api && .venv/bin/python -m pytest tests ../../packages/contracts/tests -q

# model-behaviour drift benchmark — labelled plans, real agents, costs credits
cd apps/api && .venv/bin/python tests/golden_set.py --save
```

## Branch flow

`main` is always green and always deployable — CI (`.github/workflows/ci.yml`)
runs the offline suite plus web lint and build on every push and pull request.

```
main                     always green, always deployable
 └── feat/<name>         one feature or milestone per branch
      → PR → CI green → merge (--no-ff) → main
```

Before merging anything: run the verification ritual (`/ship-check`, or
`CLAUDE.md` § How we work). Layers 2–5 (live QA hunt, golden-set drift benchmark,
Playwright E2E, adversarial review) cost model credits and run locally, not in CI.

## Deployment requirements (security)

These are enforced by configuration, not code defaults — set them in any
non-local environment:

| Variable | Value | Why |
|---|---|---|
| `MEYRAKI_HTTPS` | `1` | Adds `Secure` to the session cookie. A TLS-terminating proxy does **not** add it for you. |
| `MEYRAKI_WEB_ORIGINS` | `https://app.example.com` | CORS allow-list **and** the server-side CSRF origin check (comma-separated). |
| `MEYRAKI_TRUST_PROXY` | `1` | Only when a trusted proxy is the sole ingress. Rate limits then key on the last `X-Forwarded-For` hop instead of the proxy IP (otherwise every customer shares one bucket). |
| uvicorn flags | `--proxy-headers --forwarded-allow-ips=<lb-cidr>` | Same reason, at the server level. |
| `DATABASE_URL` | managed Postgres URL | Never fall back to the committed dev default. |

Rate limits are per process and in memory (`app/ratelimit.py`); move them to
Redis before running multiple workers behind a load balancer.
