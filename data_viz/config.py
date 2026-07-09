# Python Standard Library Dependencies
import os
from datetime import timedelta

# External Dependency Imports
from dotenv import load_dotenv

# Internal Dependency Imports


#######################################################################################
#                                        Notes:                                       #
#######################################################################################

# Configuration settings for the Flask application within the project
load_dotenv()

class Config():
    SECRET_KEY = os.environ["SECRET_KEY"]
    # Separate signing key for invite JWTs so a session-key rotation or a leak of one key doesn't
    # compromise the other. Falls back to SECRET_KEY when unset (keeps existing deployments working).
    INVITE_JWT_SECRET = os.environ.get("INVITE_JWT_SECRET") or SECRET_KEY
    # Public origin (scheme + host, no trailing slash) used to build absolute links in outbound email
    # (e.g. the invite accept link). Set to the real domain in prod, e.g. https://canask.example.ca.
    PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL") or os.environ.get("BASE_URL") or "").rstrip("/")
    DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
    ASSET_DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
    SIMPLELOGIN_LOGIN_URL = os.environ.get("SIMPLELOGIN_LOGIN_URL")
    SIMPLELOGIN_HOME_URL = os.environ.get("SIMPLELOGIN_HOME_URL")
    SIMPLELOGIN_USERNAME = os.environ.get("SIMPLELOGIN_USERNAME")
    SIMPLELOGIN_PASSWORD = os.environ.get("SIMPLELOGIN_PASSWORD")
    PERMANENT_SESSION_LIFETIME = timedelta(hours=24)
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True
    INVITE_TOKEN_EXPIRY = timedelta(minutes=10)

    # --- Rate limiting (Flask-Limiter) ---------------------------------------------------
    # Redis-backed so limits stay correct across multiple gunicorn workers. Reuses the
    # existing Redis on a dedicated DB index (/1) to stay isolated from Celery (/0).
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "redis://redis:6379/1")
    RATELIMIT_ENABLED = os.environ.get("RATELIMIT_ENABLED", "true").lower() == "true"
    RATELIMIT_HEADERS_ENABLED = True          # emit X-RateLimit-* and Retry-After
    RATELIMIT_DEFAULT = os.environ.get("RATELIMIT_DEFAULT", "200 per minute")
    # Client-IP resolution: behind Cloudflare, the real client IP is in CF-Connecting-IP.
    RATELIMIT_CLIENT_IP_HEADER = os.environ.get("RATELIMIT_CLIENT_IP_HEADER", "CF-Connecting-IP")
    # Number of trusted proxy hops for ProxyFix (Cloudflare + nginx = 2). Fixes remote_addr
    # for audit logging; the limiter itself keys off CF-Connecting-IP above.
    TRUSTED_PROXY_COUNT = int(os.environ.get("TRUSTED_PROXY_COUNT", "2"))
    # Per-route limits (tunable without a code change).
    RATELIMIT_FEEDBACK = os.environ.get("RATELIMIT_FEEDBACK", "5 per hour")            # per IP
    RATELIMIT_FEEDBACK_GLOBAL = os.environ.get("RATELIMIT_FEEDBACK_GLOBAL", "100 per day")  # all IPs, SES cost cap
    RATELIMIT_API = os.environ.get("RATELIMIT_API", "60 per minute")                   # per IP
    RATELIMIT_LOGIN = os.environ.get("RATELIMIT_LOGIN", "10 per minute")               # per IP, POST only

    # Per-account login lockout (complements the per-IP RATELIMIT_LOGIN, which a distributed attacker
    # can sidestep by rotating IPs). Counts recent failed attempts for one account; time-windowed so it
    # self-heals without an admin unlock.
    LOGIN_LOCKOUT_THRESHOLD = int(os.environ.get("LOGIN_LOCKOUT_THRESHOLD", "8"))
    LOGIN_LOCKOUT_WINDOW = timedelta(minutes=int(os.environ.get("LOGIN_LOCKOUT_WINDOW_MINUTES", "15")))

    # --- Security headers ----------------------------------------------------------------
    # Session/CSRF cookie hardening. Secure is gated on prod (DEBUG off) because local dev serves
    # plain HTTP -- a Secure cookie would never be sent and would break the dev login. HttpOnly and
    # SameSite=Lax are safe in both. REMEMBER_COOKIE_SECURE covers Flask-Login "remember me" if enabled.
    SESSION_COOKIE_SECURE = not DEBUG
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = not DEBUG
    REMEMBER_COOKIE_HTTPONLY = True

    # Content-Security-Policy. This app pulls several first- and third-party resources (see base.jinja):
    # jsDelivr (Bootstrap/Popper/icons), Google Fonts, Google reCAPTCHA, and Google Analytics/Tag Manager.
    # 'unsafe-inline' is required for scripts/styles because the templates carry inline <script> boot
    # blocks (theme bootstrap, gtag, `| tojson` deep-link data) and inline style= attributes; autoescaping
    # remains the primary XSS defense, with this CSP as defense-in-depth (notably clickjacking via
    # frame-ancestors). Tighten to nonces once the inline blocks are refactored. Tunable via env.
    CONTENT_SECURITY_POLICY = os.environ.get("CONTENT_SECURITY_POLICY", "; ".join([
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://www.google.com "
        "https://www.gstatic.com https://www.googletagmanager.com",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com",
        "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net",
        "img-src 'self' data: https:",
        "connect-src 'self' https://www.google-analytics.com https://*.google-analytics.com "
        "https://*.analytics.google.com https://www.googletagmanager.com",
        "frame-src https://www.google.com https://recaptcha.google.com",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "object-src 'none'",
        "form-action 'self'",
    ]))
    # Add more configuration settings here as the need arises

def configure(app):
    app.config.from_object(Config)

# Test code below
if __name__ == '__main__':
    pass # Replace this with function calls or test code