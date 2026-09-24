"""Production settings. Intended to run behind a TLS-terminating proxy such as a Cloudflare
Tunnel: the public site is https://<DJANGO_PUBLIC_HOSTNAME>, the tunnel forwards to gunicorn
on http://127.0.0.1:8000 (or http://web:8000 in docker compose)."""

from .base import *  # noqa: F403
from .base import MIDDLEWARE, env

DEBUG = False

# Public hostnames / CSRF trusted origins come from DJANGO_PUBLIC_HOSTNAME (see base.py).

# TLS ends at Cloudflare; the tunnel reaches us over plain HTTP and Cloudflare sends
# X-Forwarded-Proto: https. Only safe because gunicorn listens on 127.0.0.1 / the compose
# network, so every request comes through the tunnel (or from this machine).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = env.bool("DJANGO_SECURE_COOKIES", default=True)
CSRF_COOKIE_SECURE = env.bool("DJANGO_SECURE_COOKIES", default=True)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
# HTTP→HTTPS redirect and HSTS are best done at Cloudflare ("Always Use HTTPS", HSTS).
# They can also be enabled here; requests from the tunnel carry X-Forwarded-Proto: https,
# so the redirect does not loop.
SECURE_SSL_REDIRECT = env.bool("DJANGO_SSL_REDIRECT", default=False)
SECURE_HSTS_SECONDS = env.int("DJANGO_HSTS_SECONDS", default=0)
# Domain-wide commitments: only enable when every subdomain is HTTPS-only.
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = env.bool("DJANGO_HSTS_PRELOAD", default=False)

# Static files are served by WhiteNoise inside the gunicorn process.
MIDDLEWARE = [MIDDLEWARE[0], "whitenoise.middleware.WhiteNoiseMiddleware", *MIDDLEWARE[1:]]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
