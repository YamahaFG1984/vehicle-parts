"""gunicorn settings shared by scripts/serve.sh (host) and the Docker image."""

import os

# Only this machine (and the Cloudflare Tunnel running on it) can reach the app.
# The Docker image sets GUNICORN_BIND=0.0.0.0:8000 so the compose network can.
bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:8000")
workers = int(os.environ.get("GUNICORN_WORKERS", "3"))
# Cloudflare gives up on an origin response after 100 s; keep requests below that.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "95"))
graceful_timeout = 30

# Behind the tunnel every request comes from 127.0.0.1; log the visitor's real IP, which
# Cloudflare sends as CF-Connecting-IP ("-" when the request did not come through Cloudflare).
accesslog = "-"
errorlog = "-"
access_log_format = (
    '%({cf-connecting-ip}i)s %(h)s %(t)s "%(r)s" %(s)s %(b)s %(M)sms "%({user-agent}i)s"'
)
