FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Dependencies first (cached layer), production group only.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY . .
RUN DJANGO_SECRET_KEY=collectstatic DATABASE_URL=sqlite:////tmp/unused.db \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    python manage.py collectstatic --noinput \
 && useradd --create-home --uid 1000 app \
 && mkdir -p /app/media /app/exports \
 && chown -R app /app/media /app/exports \
 && chmod +x /app/scripts/entrypoint.sh

USER app
EXPOSE 8000
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
