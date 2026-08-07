# Meyraki Insight — web image.
#
# Build context is the REPO ROOT (kept consistent with the API image):
#   docker build -f apps/web/Dockerfile --build-arg NEXT_PUBLIC_API_URL=https://api.example.com -t meyraki-web .
#
# NEXT_PUBLIC_API_URL is read in the browser, so Next inlines it at BUILD time. That
# makes this image environment-specific by construction: an image built for staging
# cannot be promoted to production unchanged. Build one per environment.

FROM node:22-slim AS deps
RUN corepack enable
WORKDIR /app/apps/web
# pnpm-workspace.yaml belongs here too, not just in the builder stage. It carries
# `allowBuilds`, which is how this project declares that sharp and unrs-resolver must NOT
# run their install scripts. Without it pnpm finishes resolving, prints
# "[ERR_PNPM_IGNORED_BUILDS] Ignored build scripts: unrs-resolver" and exits non-zero —
# an install that reads as complete in the log and still fails the build.
COPY apps/web/package.json apps/web/pnpm-lock.yaml apps/web/pnpm-workspace.yaml ./
# The Next tarballs are large and this install has to survive a slow or flaky link — the
# defaults aborted mid-download and failed the whole build. These go in .npmrc because
# that is what pnpm reads; the equivalent npm_config_* environment variables are ignored
# by its fetcher, which is why raising them changed nothing.
# Only store-dir here. pnpm 11 no longer reads fetch-timeout/network-concurrency from
# .npmrc — `pnpm config get fetch-timeout` returns undefined — which is why raising them
# changed nothing, and there is no --fetch-timeout flag to use instead.
RUN printf 'store-dir=/pnpm/store\n' > .npmrc

# So resilience comes from the shape of the command rather than a setting. The store is a
# BuildKit cache mount, so each attempt resumes from whatever already downloaded, and the
# loop retries a link slow enough that pnpm's internal fetch abort fires mid-download
# (measured here at ~680 KB/s against a 37 MB tarball). On a fast network the first
# attempt succeeds and the loop costs nothing.
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store,sharing=locked \
    for attempt in 1 2 3 4 5 6; do \
      echo "pnpm install attempt $attempt" ; \
      pnpm install --frozen-lockfile --network-concurrency 1 && exit 0 ; \
      sleep 5 ; \
    done ; \
    echo "pnpm install failed after 6 attempts" ; exit 1

FROM node:22-slim AS builder
RUN corepack enable
WORKDIR /app/apps/web
COPY --from=deps /app/apps/web/node_modules ./node_modules
COPY apps/web ./

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM node:22-slim AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

WORKDIR /app
# standalone carries its own minimal node_modules; static/ and public/ are NOT included
# in it and must be copied alongside or every asset 404s.
#
# The paths matter and are easy to get wrong: apps/web is its own pnpm workspace root, so
# Next traces from there and emits server.js at the standalone ROOT — not under apps/web/.
# Copying as if it were nested produced a container that crash-looped on
# "Cannot find module '/app/apps/web/server.js'", and, once started, served an unstyled
# login page because every /_next/static/* asset 404'd. Both passed a healthcheck.
COPY --from=builder /app/apps/web/.next/standalone ./
COPY --from=builder /app/apps/web/.next/static ./.next/static
COPY --from=builder /app/apps/web/public ./public

RUN useradd --create-home --uid 10001 meyraki && chown -R meyraki:meyraki /app
USER meyraki

EXPOSE 3000
# Fetches the page AND one asset it references: serving HTML while every /_next/static
# file 404s is a real failure mode here, and a page-only check reports it as healthy.
HEALTHCHECK --interval=15s --timeout=8s --start-period=15s --retries=5 \
  CMD node -e "fetch('http://127.0.0.1:3000/login').then(async r=>{if(!r.ok)process.exit(1);const h=await r.text();const m=h.match(/\/_next\/static\/[^\"']+\.js/);if(!m)process.exit(0);const a=await fetch('http://127.0.0.1:3000'+m[0]);process.exit(a.ok?0:1)}).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
