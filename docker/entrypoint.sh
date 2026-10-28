#!/bin/sh
set -e

# Wait for PostgreSQL
host="${POSTGRES_HOST:-db}"
port="${POSTGRES_PORT:-5432}"
echo "Waiting for PostgreSQL at $host:$port..."
i=0
until nc -z "$host" "$port"; do
  i=$((i+1))
  if [ "$i" -gt 60 ]; then echo "PostgreSQL did not become available"; exit 1; fi
  sleep 1
done

case "$1" in
  web)
    python manage.py migrate --noinput
    python manage.py ensure_admin
    exec gunicorn config.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers "${GUNICORN_WORKERS:-3}" \
      --threads "${GUNICORN_THREADS:-4}" \
      --timeout "${GUNICORN_TIMEOUT:-120}" \
      --access-logfile - --error-logfile -
    ;;
  worker)
    # give the web container a moment to run migrations
    until python manage.py migrate --check >/dev/null 2>&1; do sleep 2; done
    exec python manage.py sync_mail --loop --interval "${MAILBOX_SYNC_INTERVAL:-60}"
    ;;
  *)
    exec "$@"
    ;;
esac
