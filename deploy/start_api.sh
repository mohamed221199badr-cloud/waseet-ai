#!/bin/bash
# Start command for the API on Render (or any host):
# runs migrations, optionally seeds the synthetic demo data, then serves the API.
set -e

python -m alembic upgrade head

if [ "$SEED_DEMO" = "1" ]; then
  python -m app.seed
  python -m app.seed_orders
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
