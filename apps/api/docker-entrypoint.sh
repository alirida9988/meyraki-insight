#!/bin/sh
# Bring the schema up to date, then serve. Migrations run here rather than in the
# application's startup hook so that a failed migration stops the container instead of
# leaving it serving against a half-built schema.
#
# Single-replica assumption: Alembic takes no cross-process lock, so N replicas starting
# at once would race. Run migrations as a separate one-shot step before scaling out.
set -e

echo "==> alembic upgrade head"
alembic upgrade head

echo "==> starting uvicorn"
exec "$@"
