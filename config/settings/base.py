"""Base settings shared by every environment. Secrets come from the environment only."""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SITE_NAME = "Fit Vehicle Parts 产品数据中心"

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# Public hostname(s) when the site is reached through a Cloudflare Tunnel (or any HTTPS proxy).
# Applies to every environment, so `runserver` behind a tunnel works as well as production.
# Each one is added to ALLOWED_HOSTS and, as https://<host>, to CSRF_TRUSTED_ORIGINS (needed
# for any POST, including the login form, when the page was loaded over HTTPS).
#   parts.example.com   a fixed hostname (named tunnel)
#   .trycloudflare.com  any subdomain: `cloudflared tunnel --url ...` (quick tunnel) gets a new
#                       random https://<words>.trycloudflare.com address on every start
PUBLIC_HOSTNAMES = env.list("DJANGO_PUBLIC_HOSTNAME", default=[])
ALLOWED_HOSTS = list(dict.fromkeys([*ALLOWED_HOSTS, *PUBLIC_HOSTNAMES]))
CSRF_TRUSTED_ORIGINS = list(dict.fromkeys([
    *env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[]),
    *(f"https://*{h}" if h.startswith(".") else f"https://{h}" for h in PUBLIC_HOSTNAMES),
]))
if PUBLIC_HOSTNAMES:
    # TLS ends at Cloudflare, which sends X-Forwarded-Proto: https through the tunnel.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "django.contrib.humanize",
]
LOCAL_APPS = [
    "apps.core",
    "apps.ingestion",
    "apps.catalog",
    "apps.matching",
    "apps.exports",
]
INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site",
            ],
        },
    },
]

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DJANGO_CONN_MAX_AGE", default=60)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "core.User"
LOGIN_URL = "login"
LOGOUT_REDIRECT_URL = "login"
LOGIN_REDIRECT_URL = "catalog:dashboard"
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "America/Chicago"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("DJANGO_MEDIA_ROOT", default=str(BASE_DIR / "media")))

# Tunable business rules live outside the code (see docs/design.html §7).
RULES_DIR = Path(env("VP_RULES_DIR", default=str(BASE_DIR / "config" / "rules")))
EXPORT_DIR = Path(env("VP_EXPORT_DIR", default=str(BASE_DIR / "exports")))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "loggers": {"apps": {"handlers": ["console"], "level": env("VP_LOG_LEVEL", default="INFO")}},
}
