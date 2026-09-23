from .base import *  # noqa: F403
from .base import MIDDLEWARE, env

DEBUG = False
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = env.bool("DJANGO_SECURE_COOKIES", default=True)
CSRF_COOKIE_SECURE = env.bool("DJANGO_SECURE_COOKIES", default=True)
SECURE_CONTENT_TYPE_NOSNIFF = True
# Enable once the site is served over HTTPS end to end (usually behind a reverse proxy).
SECURE_SSL_REDIRECT = env.bool("DJANGO_SSL_REDIRECT", default=False)
SECURE_HSTS_SECONDS = env.int("DJANGO_HSTS_SECONDS", default=0)

# Static files are served by WhiteNoise inside the gunicorn container.
MIDDLEWARE = [MIDDLEWARE[0], "whitenoise.middleware.WhiteNoiseMiddleware", *MIDDLEWARE[1:]]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
