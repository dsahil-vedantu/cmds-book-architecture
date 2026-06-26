#!/bin/sh
# Single-container startup: runs Celery worker + uvicorn API together so
# they share the same filesystem (and therefore the same Railway volume
# mounted at /app/storage). Eliminates the "PDF not found" class of bugs
# that arose when api and worker ran in separate containers with
# separate filesystems.
#
# Process layout:
#   • Celery worker  → background, SUPERVISED (auto-relaunched if it ever exits)
#   • uvicorn API    → foreground (PID 1 so Railway sees it for healthchecks)
#
# CRITICAL — worker supervision: uvicorn is PID 1, so Railway's healthcheck
# only watches uvicorn. If the Celery worker dies (OOM, crash) the container
# still looks healthy and Railway never restarts it — leaving NO worker to
# consume the queue, so every task sits "queued — waiting for worker" forever.
# The supervisor loop below relaunches the worker whenever it exits.
#
# NOTE: this catches CRASHES/EXITS. A worker that HANGS (alive but stuck) is
# mitigated separately by capping DB connection pools (so concurrent load
# can't exhaust Postgres and block the worker) and by UVICORN_WORKERS=1.
#
# NOTE: deliberately NO `set -e` — a non-zero exit from the supervised worker
# must NOT kill this script; the loop handles it.

CELERY_CONCURRENCY="${CELERY_CONCURRENCY:-2}"
LOG_LEVEL="${LOG_LEVEL:-info}"

# UVICORN_WORKERS defaults to 1: the watchdog + orphan-recovery startup hooks
# run once PER uvicorn worker and are NOT multi-worker-safe (no cross-process
# lock) — 2 workers = 2 watchdog driver loops = double the DB connections +
# duplicate orphan re-dispatch. One async uvicorn worker handles plenty of
# concurrent HTTP via the event loop. Override only after the hooks are made
# multi-worker-safe.
UVICORN_WORKERS="${UVICORN_WORKERS:-1}"

# ── Supervised Celery worker ────────────────────────────────────────────────
supervise_worker() {
  while true; do
    echo "[start.sh] starting celery worker (concurrency=$CELERY_CONCURRENCY, log=$LOG_LEVEL)"
    celery -A app.workers.celery_app worker \
      --loglevel="$LOG_LEVEL" \
      --concurrency="$CELERY_CONCURRENCY" \
      --without-gossip \
      --without-mingle \
      --without-heartbeat
    code=$?
    echo "[start.sh] celery worker EXITED (code=$code) — relaunching in 3s"
    sleep 3
  done
}
supervise_worker &
SUP_PID=$!

# Forward shutdown signals so the supervisor (and its current worker) stop
# cleanly instead of being relaunched during a deploy.
trap 'echo "[start.sh] SIGTERM → stopping worker supervisor (pid=$SUP_PID)"; kill -TERM "$SUP_PID" 2>/dev/null; exit 0' TERM INT

echo "[start.sh] launching uvicorn on port ${PORT:-8000} (workers=$UVICORN_WORKERS)"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers "$UVICORN_WORKERS"
