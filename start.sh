#!/usr/bin/env bash
set -euo pipefail
python scripts/db_preflight.py
# One worker/thread avoids concurrent SQLite writes on one persistent Render disk.
# For higher traffic, migrate to PostgreSQL and adjust concurrency after load tests.
exec gunicorn --bind "0.0.0.0:${PORT:-10000}" --workers 1 --threads 1 --timeout 90 app:app
