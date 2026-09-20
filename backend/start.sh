#!/bin/sh
#
# Production entrypoint — Render, Cloud Run, any plain Docker host.
#
# docker-compose replaces this with its own reload-enabled `command:`, so this
# script runs only where the image's CMD is used, which is everywhere but local
# development.
set -e

# Migrations run on boot rather than as a separate deploy step, because a
# free-tier host starts the container on its own schedule — after an idle
# suspend, or a platform-side restart — with no deploy hook to hang a migration
# off. `alembic upgrade head` is a no-op once the schema is current, so the cost
# is a few seconds on a cold start.
echo "▶ Applying database migrations..."
alembic upgrade head

# $PORT is assigned by the platform at runtime and is not knowable at build
# time; the fallback is for a bare `docker run`. No --reload: it watches the
# filesystem and holds a second copy of the app in memory, which on a 512MB
# instance is worth real money in cold-start time.
echo "▶ Starting uvicorn on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
