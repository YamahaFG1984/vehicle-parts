#!/bin/sh
# Run the site in production mode on this machine (no Docker), for a Cloudflare Tunnel:
# gunicorn on 127.0.0.1:8000, DEBUG off, static files via WhiteNoise.
# Needs in .env: DJANGO_SECRET_KEY (a long random value), DATABASE_URL, DJANGO_PUBLIC_HOSTNAME;
# optional GUNICORN_BIND (default 127.0.0.1:8000), e.g. 127.0.0.1:8010 for
# `cloudflared tunnel --url http://127.0.0.1:8010`.
set -e
cd "$(dirname "$0")/.."
if [ -f .env ]; then  # make .env values (e.g. GUNICORN_BIND) visible to gunicorn's config too
    set -a; . ./.env; set +a
fi
export DJANGO_SETTINGS_MODULE=config.settings.production
uv run python manage.py check --deploy --fail-level ERROR
uv run python manage.py migrate --noinput
uv run python manage.py collectstatic --noinput --verbosity 0
exec uv run gunicorn config.wsgi:application -c config/gunicorn.conf.py
