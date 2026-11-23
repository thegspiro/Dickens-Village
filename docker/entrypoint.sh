#!/bin/sh
# Container entrypoint: apply pending migrations, then start the web server.
# POSIX sh only. Migrations hold a MySQL lock, so starting several containers
# at once is safe.
set -eu

: "${GUNICORN_WORKERS:=2}"
# Threads per worker. A slow upload (a big 3D scan over a phone connection) then
# ties up one thread instead of a whole worker, so the site stays responsive.
: "${GUNICORN_THREADS:=4}"
: "${GUNICORN_TIMEOUT:=120}"  # long enough for several large photos over a slow link

case "$GUNICORN_WORKERS" in
  ''|*[!0-9]*) echo "GUNICORN_WORKERS must be a whole number" >&2; exit 1 ;;
esac
case "$GUNICORN_THREADS" in
  ''|*[!0-9]*|0) echo "GUNICORN_THREADS must be a whole number of at least 1" >&2; exit 1 ;;
esac
case "$GUNICORN_TIMEOUT" in
  ''|*[!0-9]*) echo "GUNICORN_TIMEOUT must be a whole number" >&2; exit 1 ;;
esac

if [ "$#" -gt 0 ]; then
  # Allow one-off commands, e.g. `docker compose run --rm app python -m app.hashpw`.
  exec "$@"
fi

echo "Applying database migrations..."
python -m app.migrate up

echo "Loading reference catalog..."
python -m app.catalog sync

echo "Starting web server on port 8000 with $GUNICORN_WORKERS worker(s) x $GUNICORN_THREADS thread(s)"
exec gunicorn \
  --bind 0.0.0.0:8000 \
  --workers "$GUNICORN_WORKERS" \
  --worker-class gthread \
  --threads "$GUNICORN_THREADS" \
  --timeout "$GUNICORN_TIMEOUT" \
  --access-logfile - \
  --error-logfile - \
  "app.wsgi:app"
