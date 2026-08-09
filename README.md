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
| `MEYRAKI_SHARE_SECRET` | long random string | Signs the expiring links a studio sends to a client who has no account. **Unset means sharing is off** — deliberately, because a predictable signing key is worse than no sharing. Rotating it invalidates every link already sent. |
| `MEYRAKI_HTTPS` | `1` | Adds `Secure` to the session cookie. A TLS-terminating proxy does **not** add it for you. |
| `HUGGINGFACE_API_TOKEN` | `hf_…` | Routes moodboard renders to FLUX.1-Krea-dev — **final**, client-presentable quality (~$0.025/render, so ~$0.075 per report). Without it renders fall to the free tier, which is captioned as draft in the client report. A free HF account's included credit runs out after a handful of images; top up pre-paid credits or subscribe to PRO. |
| `MEYRAKI_FLUX_MODEL` | `fal-ai/flux/krea` | Which FLUX model serves renders. Drop to `fal-ai/flux/schnell` ($0.003/MP instead of $0.025) when volume matters more than fidelity. |
| `MEYRAKI_ANALYSIS_BUDGET_USD` | `1.50` | Per-analysis model-spend ceiling. Checked before every step; the run stops rather than overspending, and the receipt is served on `GET /analyses/{id}`. |
| `MEYRAKI_WEB_ORIGINS` | `https://app.example.com` | CORS allow-list **and** the server-side CSRF origin check (comma-separated). |
| `MEYRAKI_TRUST_PROXY` | `1` | Only when a trusted proxy is the sole ingress. Rate limits then key on the last `X-Forwarded-For` hop instead of the proxy IP (otherwise every customer shares one bucket). |
| uvicorn flags | `--proxy-headers --forwarded-allow-ips=<lb-cidr>` | Same reason, at the server level. |
| `DATABASE_URL` | managed Postgres URL | Never fall back to the committed dev default. |

Rate limits are per process and in memory (`app/ratelimit.py`); move them to
Redis before running multiple workers behind a load balancer.

## Deployment (Docker)

```bash
cp .env.example .env          # fill in POSTGRES_PASSWORD and ANTHROPIC_API_KEY at minimum
docker compose up --build     # db + api + web, on 127.0.0.1 only
```

| Service | Image | Notes |
|---|---|---|
| `db` | `postgres:16-alpine` | Named volume; `pg_isready -U … -d meyraki` gates the API's start. |
| `api` | `apps/api/Dockerfile` | Carries a real Chromium — the report PDF is rendered by Playwright so it uses the web design system verbatim. Non-root, health-checked, uploads on a named volume. |
| `web` | `apps/web/Dockerfile` | Next.js `output: "standalone"`. Non-root, health-checked. |

Two things that bite if you skip them:

- **`NEXT_PUBLIC_API_URL` is baked in at build time**, because the browser reads it. The
  web image is therefore environment-specific — a staging image cannot be promoted to
  production unchanged. Build one per environment, and use the URL the *browser* can
  reach, never the compose service name.
- **The base compose file publishes on `127.0.0.1` only** and terminates no TLS. It is a
  development stack. For anything reachable from outside the host, use the production
  overlay below rather than opening those ports.

### Deploying with TLS

```bash
cp .env.example .env      # set APP_DOMAIN, API_DOMAIN, ACME_EMAIL and the keys
GIT_SHA=$(git rev-parse --short HEAD) \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

The overlay adds Caddy, which obtains and renews Let's Encrypt certificates by itself —
no cron job and no renewal hook, because the certificate that silently fails to renew is
the classic self-hosted outage. It also removes the API and web host ports entirely, so
the only thing on a public interface is the proxy on 80 and 443, and CI asserts that.

It sets `MEYRAKI_HTTPS=1`, `MEYRAKI_TRUST_PROXY=1`, `MEYRAKI_WEB_ORIGINS` and
`NEXT_PUBLIC_API_URL` for you from the two domain names. Do not also set them by hand.
Without the first the session cookie ships without `Secure`; without the second the rate
limiter sees the proxy's IP as every client, so one visitor can exhaust everyone's quota.

**Both names must share a registrable domain** — `app.meyraki.com` and `api.meyraki.com`,
not `meyraki-app.com` and `meyraki-api.com`. The session cookie is `SameSite=Lax`, which
browsers judge per *site* rather than per origin: subdomains of one domain are same-site
and the cookie is sent; two unrelated domains are not, and every signed-in request would
arrive without a session while looking, in the logs, like a login problem.

Point both names at the host with A records before starting, or the ACME challenge fails
and Let's Encrypt rate-limits retries to five per domain per week.

**Still a single host.** The overlay runs Postgres in a container with a volume. That is a
real database and it works, but there is no automated restore, and client floorplans
should not live on one machine indefinitely — managed Postgres and object storage are the
remaining deployment decisions.

### Schema changes

Alembic owns the schema on Postgres. The API image runs `alembic upgrade head` on start,
before uvicorn, so a failed migration stops the container rather than leaving it serving
against a half-built schema.

```bash
cd apps/api
alembic revision --autogenerate -m "what changed"   # after editing app/models.py
alembic upgrade head                                 # apply
alembic check                                        # models and migrations agree? CI runs this
```

`init_db()` still calls `create_all`, but **only on SQLite** — the test and local path,
where a schema is built and discarded in the same second. On any other database it does
nothing, because `create_all` there is a trap: it makes the tables but writes no
`alembic_version` row, so the next `alembic upgrade head` tries to create tables that
already exist and fails. It also never ALTERs anything, which is how this repo's own dev
database silently lost an index and a foreign key the models declare.

**An existing database that predates Alembic** needs stamping once, or the baseline
migration will try to recreate its tables:

```bash
alembic stamp head        # "this database is already at the baseline"
alembic check             # then confirm it really matches the models
```
