"""Production settings for a Cloudflare Tunnel: https://<public host> → http://127.0.0.1:8000."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import Client, override_settings

ROOT = Path(__file__).resolve().parent.parent
HOST = "parts.example.com"


def _production(code: str, **env) -> subprocess.CompletedProcess:
    full_env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.production",
                "DJANGO_SECRET_KEY": "x" * 60 + "prod-secret-for-tests-9f3k2", **env}
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=full_env,
                          capture_output=True, text=True, timeout=120)


def test_public_hostname_configures_hosts_and_csrf():
    proc = _production(
        "import django, json; django.setup(); from django.conf import settings as s; "
        "print(json.dumps({k: getattr(s, k) for k in ('DEBUG', 'ALLOWED_HOSTS', "
        "'CSRF_TRUSTED_ORIGINS', 'SECURE_PROXY_SSL_HEADER', 'SESSION_COOKIE_SECURE', "
        "'CSRF_COOKIE_SECURE')}))",
        DJANGO_PUBLIC_HOSTNAME=HOST)
    assert proc.returncode == 0, proc.stderr
    s = json.loads(proc.stdout.strip().splitlines()[-1])
    assert s["DEBUG"] is False
    assert HOST in s["ALLOWED_HOSTS"] and "127.0.0.1" in s["ALLOWED_HOSTS"]
    assert s["CSRF_TRUSTED_ORIGINS"] == [f"https://{HOST}"]
    assert s["SECURE_PROXY_SSL_HEADER"] == ["HTTP_X_FORWARDED_PROTO", "https"]
    assert s["SESSION_COOKIE_SECURE"] and s["CSRF_COOKIE_SECURE"]


DEPLOY_CHECK = ("import django; from django.core.management import call_command; django.setup(); "
                "call_command('check', deploy=True, fail_level='WARNING')")
HTTPS_OPTIONS = {"W004", "W005", "W008", "W021"}  # HSTS / SSL redirect: set at Cloudflare or .env


def test_deploy_check_is_clean_with_https_options():
    proc = _production(DEPLOY_CHECK, DJANGO_PUBLIC_HOSTNAME=HOST, DJANGO_SSL_REDIRECT="true",
                       DJANGO_HSTS_SECONDS="31536000", DJANGO_HSTS_INCLUDE_SUBDOMAINS="true",
                       DJANGO_HSTS_PRELOAD="true")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_deploy_check_only_warns_about_optional_https_options():
    import re

    proc = _production(DEPLOY_CHECK, DJANGO_PUBLIC_HOSTNAME=HOST)
    warnings = set(re.findall(r"security\.(W\d+)", proc.stderr))
    assert warnings <= HTTPS_OPTIONS, proc.stderr


def _gunicorn_conf() -> dict:
    ns: dict = {}
    exec((ROOT / "config" / "gunicorn.conf.py").read_text(), ns)
    return ns


def test_gunicorn_listens_only_on_this_machine(monkeypatch):
    monkeypatch.delenv("GUNICORN_BIND", raising=False)  # .env may set it for this machine
    ns = _gunicorn_conf()
    assert ns["bind"] == "127.0.0.1:8000"
    monkeypatch.setenv("GUNICORN_BIND", "127.0.0.1:8010")  # cloudflared --url http://127.0.0.1:8010
    assert _gunicorn_conf()["bind"] == "127.0.0.1:8010"
    assert "cf-connecting-ip" in ns["access_log_format"]
    assert ns["timeout"] < 100  # Cloudflare's origin response limit


TUNNEL = dict(ALLOWED_HOSTS=[HOST], CSRF_TRUSTED_ORIGINS=[f"https://{HOST}"],
              SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
              SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True)
# What Cloudflare puts on a request arriving through the tunnel.
VIA_TUNNEL = {"HTTP_HOST": HOST, "HTTP_X_FORWARDED_PROTO": "https",
              "HTTP_CF_CONNECTING_IP": "203.0.113.7"}


def _login(client, extra):
    page = client.get("/accounts/login/", **extra)
    token = page.cookies["csrftoken"].value
    return client.post("/accounts/login/", {
        "username": "zhang.wei", "password": "pw-12345-login", "next": "/",
        "csrfmiddlewaretoken": token,
    }, HTTP_ORIGIN=f"https://{HOST}", HTTP_REFERER=f"https://{HOST}/accounts/login/", **extra)


@pytest.mark.django_db
@override_settings(**TUNNEL)
def test_login_through_the_tunnel(django_user_model):
    django_user_model.objects.create_user("zhang.wei", password="pw-12345-login")
    client = Client(enforce_csrf_checks=True)
    resp = _login(client, VIA_TUNNEL)
    assert resp.status_code == 302 and resp["Location"] == "/"
    assert resp.cookies["sessionid"]["secure"]  # session cookie only travels over HTTPS
    assert client.get("/", **VIA_TUNNEL).status_code == 200


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOST], CSRF_TRUSTED_ORIGINS=[], SECURE_PROXY_SSL_HEADER=None)
def test_without_proxy_settings_login_is_rejected(django_user_model):
    # The failure mode the settings prevent: HTTPS origin, but Django believes it is HTTP.
    django_user_model.objects.create_user("zhang.wei", password="pw-12345-login")
    resp = _login(Client(enforce_csrf_checks=True), VIA_TUNNEL)
    assert resp.status_code == 403


@pytest.mark.django_db
@override_settings(**TUNNEL)
def test_unknown_host_is_rejected():
    assert Client().get("/accounts/login/", HTTP_HOST="evil.example.net").status_code == 400


def test_quick_tunnel_wildcard():
    proc = _production(
        "import django, json; django.setup(); from django.conf import settings as s; "
        "print(json.dumps([s.ALLOWED_HOSTS, s.CSRF_TRUSTED_ORIGINS]))",
        DJANGO_PUBLIC_HOSTNAME=".trycloudflare.com")
    assert proc.returncode == 0, proc.stderr
    hosts, origins = json.loads(proc.stdout.strip().splitlines()[-1])
    assert ".trycloudflare.com" in hosts
    assert origins == ["https://*.trycloudflare.com"]


QUICK = dict(TUNNEL, ALLOWED_HOSTS=[".trycloudflare.com"],
             CSRF_TRUSTED_ORIGINS=["https://*.trycloudflare.com"])


@pytest.mark.django_db
@override_settings(**QUICK)
@pytest.mark.parametrize("host", ["plain-words-random-name.trycloudflare.com",
                                  "another-new-address.trycloudflare.com"])
def test_login_through_a_quick_tunnel(django_user_model, host):
    # `cloudflared tunnel --url` hands out a new random hostname on every start.
    django_user_model.objects.create_user("zhang.wei", password="pw-12345-login")
    client = Client(enforce_csrf_checks=True)
    extra = {**VIA_TUNNEL, "HTTP_HOST": host}
    page = client.get("/accounts/login/", **extra)
    resp = client.post("/accounts/login/", {
        "username": "zhang.wei", "password": "pw-12345-login", "next": "/",
        "csrfmiddlewaretoken": page.cookies["csrftoken"].value,
    }, HTTP_ORIGIN=f"https://{host}", HTTP_REFERER=f"https://{host}/accounts/login/", **extra)
    assert resp.status_code == 302 and resp["Location"] == "/"


@pytest.mark.django_db
@override_settings(**QUICK)
def test_quick_tunnel_rejects_other_hosts():
    assert Client().get("/accounts/login/", HTTP_HOST="trycloudflare.com.evil.net").status_code == 400


def test_runserver_settings_also_trust_the_tunnel():
    # `runserver` (local settings) behind a quick tunnel must accept the login POST too.
    proc = _production(
        "import django, json; django.setup(); from django.conf import settings as s; "
        "print(json.dumps([s.ALLOWED_HOSTS, s.CSRF_TRUSTED_ORIGINS, s.DEBUG]))",
        DJANGO_SETTINGS_MODULE="config.settings.local", DJANGO_PUBLIC_HOSTNAME=".trycloudflare.com")
    assert proc.returncode == 0, proc.stderr
    hosts, origins, debug = json.loads(proc.stdout.strip().splitlines()[-1])
    assert ".trycloudflare.com" in hosts and "https://*.trycloudflare.com" in origins
    assert debug is True
