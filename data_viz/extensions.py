# Flask extension singletons that must be importable without importing the app.
#
# The app is created at import time in data_viz/__init__.py and the route blueprints
# (data_viz/main.py, data_viz/auth/auth.py) need to reference the limiter to decorate
# routes. Keeping the Limiter here -- with no app dependency -- avoids an import cycle;
# data_viz/__init__.py calls limiter.init_app(app) once the app + config exist.

from flask import request, current_app
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address


def client_ip():
    """Rate-limit key = the true client IP.

    In production the app sits behind Cloudflare -> nginx, so request.remote_addr is a
    proxy IP. Cloudflare sets CF-Connecting-IP to the real client IP independent of the
    number of hops, so we prefer it (header name is configurable via
    RATELIMIT_CLIENT_IP_HEADER) and fall back to the ProxyFix-corrected remote_addr.

    Trust note: this header is only trustworthy if the origin accepts traffic solely from
    Cloudflare (see the deploy runbook) -- otherwise a direct-to-origin request could spoof
    it. The ProxyFix fallback keeps local/dev correct where no such header is present.
    """
    header = (current_app.config.get("RATELIMIT_CLIENT_IP_HEADER") or "").strip()
    if header:
        forwarded = request.headers.get(header)
        if forwarded:
            # A forwarding header may carry a comma-separated list; the client is the first entry.
            return forwarded.split(",")[0].strip()
    return get_remote_address()


# Configured entirely from app.config (RATELIMIT_* keys) in limiter.init_app(app).
limiter = Limiter(key_func=client_ip)
