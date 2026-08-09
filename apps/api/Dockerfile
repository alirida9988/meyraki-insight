# Meyraki Insight — API image.
#
# Build context is the REPO ROOT, not this directory: the API depends on the local
# `packages/contracts` package, so both must be visible to the build.
#   docker build -f apps/api/Dockerfile -t meyraki-api .
#
# The report step renders its PDF through Playwright, so this image carries a real
# Chromium. That is what makes it large (~1.4 GB) and it is not optional — a headless
# browser is how the report gets the web design system verbatim instead of a second,
# drifting stylesheet (docs/04 §Playwright).

# ---------------------------------------------------------------- build the venv
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Wheels cover every dependency on this platform; the compiler is here only so a
# missing wheel fails loudly at build time instead of at pip-install time on deploy.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Two installs on purpose. The first resolves and downloads the dependency tree from the
# manifests alone, so editing application code reuses that layer — and it is the slow
# one (ortools, jupedsim, playwright).
COPY packages/contracts/pyproject.toml /src/packages/contracts/
COPY packages/contracts/src /src/packages/contracts/src
COPY apps/api/pyproject.toml /src/apps/api/
COPY apps/api/app/__init__.py /src/apps/api/app/
RUN pip install /src/packages/contracts /src/apps/api

# The second installs the real source. Without it site-packages holds an `app` package
# containing only __init__.py — a stub that happens to be shadowed by the working
# directory at runtime, which is not a property to depend on.
COPY packages/contracts /src/packages/contracts
COPY apps/api/app /src/apps/api/app
RUN pip install --no-deps --force-reinstall /src/packages/contracts /src/apps/api

# jupedsim hard-requires PySide6 and VTK for its desktop viewer (jpsvis) — 1.3 GB of Qt
# bindings and 3D visualisation that a headless API can never use, and cannot even
# initialise without a display. We import only the simulation core, so they come out.
# Measured: the venv drops 1.9 GB -> 590 MB and the simulation is byte-identical
# (same funnel detection, same guest count). If a future jupedsim starts importing them
# at module level this breaks loudly, so CI runs a real simulation inside the image.
RUN pip uninstall -y pyside6 PySide6_Essentials PySide6_Addons shiboken6 vtk matplotlib \
 && python -c "import jupedsim; print('jupedsim imports headless:', jupedsim.__version__)"

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Browsers outside $HOME so the non-root user can read them without owning /root.
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    UPLOAD_DIR=/data/uploads

COPY --from=builder /opt/venv /opt/venv

# Chromium plus its system libraries. Driven by the pinned playwright wheel already in
# the venv, so the browser always matches the client that drives it.
RUN playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY packages/contracts /app/packages/contracts
COPY apps/api /app/apps/api

# Which commit this image was built from. A container carries no git history, so the sha
# has to be baked in at build time or the running instance cannot say what it is:
#   docker build --build-arg GIT_SHA=$(git rev-parse --short HEAD) ...
# Declared AFTER the COPY layers on purpose — the value changes on every commit, and
# putting it earlier would invalidate the dependency and browser layers each rebuild.
ARG GIT_SHA=""
ENV MEYRAKI_GIT_SHA=$GIT_SHA

# Non-root, and it must own the upload volume mountpoint or the first save fails.
RUN useradd --create-home --uid 10001 meyraki \
 && mkdir -p /data/uploads \
 && chown -R meyraki:meyraki /data /app \
 && chmod -R a+rX /ms-playwright
USER meyraki

WORKDIR /app/apps/api
EXPOSE 8000

# Uses urllib rather than curl: nothing else in this image needs curl, and an image
# should not carry a network tool just to answer its own health check.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

# --proxy-headers so the session cookie's Secure flag and the rate limiter see the real
# client scheme and IP behind a TLS-terminating proxy (README § Deployment requirements).
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
