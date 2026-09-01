# Python Standard Library Dependencies
import logging
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


def _public_base_url():
    """Read + normalize PUBLIC_BASE_URL and enforce its invariant (scheme + host, no trailing slash).
    A malformed value would only surface as a broken link in an invite email, so fail loudly at
    startup instead: raise on anything set that isn't an http(s) origin."""
    value = (os.environ.get("PUBLIC_BASE_URL") or os.environ.get("BASE_URL") or "").rstrip("/")
    if value and not value.startswith(("http://", "https://")):
        logging.getLogger(__name__).error("PUBLIC_BASE_URL %r is not an http(s) origin", value)
        raise ValueError(
            f"PUBLIC_BASE_URL must start with http:// or https:// (got {value!r}); "
            "e.g. https://canask.example.ca")
    return value


class Config():
    SECRET_KEY = os.environ["SECRET_KEY"]
    # Separate signing key for invite JWTs so a session-key rotation or a leak of one key doesn't
    # compromise the other. Falls back to SECRET_KEY when unset (keeps existing deployments working);
    # configure() logs a startup warning when the fallback engages outside DEBUG so the degraded
    # key-separation is visible to the operator.
    INVITE_JWT_SECRET = os.environ.get("INVITE_JWT_SECRET") or SECRET_KEY
    # Separate signing key for password-reset JWTs, same rationale as INVITE_JWT_SECRET. The
    # "purpose" claim in PasswordResets.generate_jwt keeps reset tokens non-interchangeable with
    # invite tokens even when both fall back to SECRET_KEY.
    PASSWORD_RESET_JWT_SECRET = os.environ.get("PASSWORD_RESET_JWT_SECRET") or SECRET_KEY
    # Public origin used to build absolute links in outbound email (e.g. the invite accept link).
    # Set to the real domain in prod, e.g. https://canask.example.ca. Validated at import.
    PUBLIC_BASE_URL = _public_base_url()
    # reCAPTCHA v3 (invisible + score) protects the login and feedback POSTs. Site key is public
    # (rendered into the page); its matching secret is RECAPTCHA_SECRET -- both must come from the
    # SAME v3 registration. Env-driven so prod uses its own registration without editing the tracked
    # template. RECAPTCHA_ENABLED=false bypasses reCAPTCHA entirely (dev, where the old v2 keys can't
    # verify against v3); MIN_SCORE is the reject threshold (0.0-1.0, Google's default is 0.5).
    RECAPTCHA_SITE_KEY = os.environ.get("RECAPTCHA_SITE_KEY", "6LckJoIqAAAAAAObcRxSz27BVJDSbg7pPID2V0jJ")
    RECAPTCHA_SECRET = os.environ.get("RECAPTCHA_SECRET")
    RECAPTCHA_MIN_SCORE = float(os.environ.get("RECAPTCHA_MIN_SCORE", "0.5"))
    RECAPTCHA_ENABLED = os.environ.get("RECAPTCHA_ENABLED", "true").lower() == "true"
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
    INVITE_TOKEN_EXPIRY = timedelta(hours=int(os.environ.get("INVITE_EXPIRY_HOURS", "72")))
    PASSWORD_RESET_EXPIRY = timedelta(minutes=int(os.environ.get("PASSWORD_RESET_EXPIRY_MINUTES", "60")))

    # --- Rate limiting (Flask-Limiter) ---------------------------------------------------
    # Redis-backed so limits stay correct across multiple gunicorn workers. Reuses the
    # existing Redis on a dedicated DB index (/1) to stay isolated from Celery (/0).
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "redis://redis:6379/1")
    RATELIMIT_ENABLED = os.environ.get("RATELIMIT_ENABLED", "true").lower() == "true"
    RATELIMIT_HEADERS_ENABLED = True          # emit X-RateLimit-* and Retry-After
    # If Redis dies after startup, degrade to per-process in-memory counting instead of 500ing every
    # request (swallow_errors defaults to False, and RATELIMIT_DEFAULT applies globally). Flask-Limiter
    # logs the storage errors on its own logger, so the outage is visible while the site stays up.
    RATELIMIT_IN_MEMORY_FALLBACK_ENABLED = True
    RATELIMIT_DEFAULT = os.environ.get("RATELIMIT_DEFAULT", "200 per minute")
    # Client-IP resolution: behind Cloudflare, the real client IP is in CF-Connecting-IP.
    RATELIMIT_CLIENT_IP_HEADER = os.environ.get("RATELIMIT_CLIENT_IP_HEADER", "CF-Connecting-IP")
    # Number of trusted proxy hops for ProxyFix (Cloudflare + nginx = 2). Fixes remote_addr
    # for audit logging; the limiter itself keys off CF-Connecting-IP above.
    TRUSTED_PROXY_COUNT = int(os.environ.get("TRUSTED_PROXY_COUNT", "2"))
    # Recipient inbox for feedback-form emails (must be an SES-verified identity while the SES
    # account is in sandbox mode).
    FEEDBACK_EMAIL = os.environ.get("FEEDBACK_EMAIL", "spencer.fietz@ucalgary.ca")
    # Per-route limits (tunable without a code change).
    RATELIMIT_FEEDBACK = os.environ.get("RATELIMIT_FEEDBACK", "5 per hour")            # per IP
    RATELIMIT_FEEDBACK_GLOBAL = os.environ.get("RATELIMIT_FEEDBACK_GLOBAL", "100 per day")  # all IPs, SES cost cap
    RATELIMIT_PASSWORD_RESET = os.environ.get("RATELIMIT_PASSWORD_RESET", "5 per hour")            # per IP
    RATELIMIT_PASSWORD_RESET_GLOBAL = os.environ.get("RATELIMIT_PASSWORD_RESET_GLOBAL", "100 per day")  # all IPs, SES cost cap
    RATELIMIT_API = os.environ.get("RATELIMIT_API", "60 per minute")                   # per IP
    RATELIMIT_LOGIN = os.environ.get("RATELIMIT_LOGIN", "10 per minute")               # per IP, POST only

    # Per-account login lockout (complements the per-IP RATELIMIT_LOGIN, which a distributed attacker
    # can sidestep by rotating IPs). Counts recent failed attempts for one account; time-windowed so it
    # self-heals without an admin unlock.
    LOGIN_LOCKOUT_THRESHOLD = int(os.environ.get("LOGIN_LOCKOUT_THRESHOLD", "8"))
    LOGIN_LOCKOUT_WINDOW = timedelta(minutes=int(os.environ.get("LOGIN_LOCKOUT_WINDOW_MINUTES", "15")))
    # The account-wide lockout threshold is deliberately higher than the per-(account, IP) one: locking
    # on account alone would let anyone who knows a victim's email lock them out at will. Per-IP locks
    # trip first; the account-wide ceiling still bounds a distributed (IP-rotating) attack.
    LOGIN_LOCKOUT_ACCOUNT_THRESHOLD = (int(os.environ.get("LOGIN_LOCKOUT_ACCOUNT_THRESHOLD", "0"))
                                       or LOGIN_LOCKOUT_THRESHOLD * 4)

    # Lockout for the shared site admin key (all gated site-admin membership endpoints). Stricter
    # than login's: the actors are already-authenticated admins confirming a destructive action.
    # The secret is shared, so a global ceiling bounds a brute force spread across several
    # compromised admin accounts; the per-actor threshold trips first.
    SITE_ADMIN_KEY_LOCKOUT_THRESHOLD = int(os.environ.get("SITE_ADMIN_KEY_LOCKOUT_THRESHOLD", "5"))
    SITE_ADMIN_KEY_LOCKOUT_WINDOW = timedelta(minutes=int(os.environ.get("SITE_ADMIN_KEY_LOCKOUT_WINDOW_MINUTES", "15")))
    SITE_ADMIN_KEY_LOCKOUT_GLOBAL_THRESHOLD = (int(os.environ.get("SITE_ADMIN_KEY_LOCKOUT_GLOBAL_THRESHOLD", "0"))
                                        or SITE_ADMIN_KEY_LOCKOUT_THRESHOLD * 4)

    # --- Security headers ----------------------------------------------------------------
    # Session/CSRF cookie hardening. Secure defaults ON and is its own knob (COOKIE_SECURE) rather
    # than riding on DEBUG -- a mis-set DEBUG=true in prod shouldn't silently strip Secure from the
    # cookies too. Local dev serves plain HTTP, so .env.dev sets COOKIE_SECURE=false there (a Secure
    # cookie would never be sent and would break the dev login). HttpOnly and SameSite=Lax are safe
    # in both. REMEMBER_COOKIE_SECURE covers Flask-Login "remember me" if enabled.
    COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() == "true"
    SESSION_COOKIE_SECURE = COOKIE_SECURE
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = COOKIE_SECURE
    REMEMBER_COOKIE_HTTPONLY = True

    # Reject oversized request bodies in the app itself (Flask returns 413), independent of the prod
    # nginx front (client_max_body_size 2m). Keeps the validators' per-character scans bounded even
    # when the app is reached without nginx (dev, direct gunicorn).
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024

    # Content-Security-Policy. This app pulls several first- and third-party resources (see base.jinja):
    # jsDelivr (Bootstrap/Popper/icons), Google Fonts, Google reCAPTCHA, and Google Analytics/Tag Manager.
    # 'unsafe-inline' is required for scripts/styles because the templates carry inline <script> boot
    # blocks (theme bootstrap, gtag, `| tojson` deep-link data) and inline style= attributes; autoescaping
    # remains the primary XSS defense, with this CSP as defense-in-depth (notably clickjacking via
    # frame-ancestors). 'unsafe-eval' is required because htmx evaluates hx-on: attributes via
    # new Function() -- the admin modals (adjust permissions/roles) open through hx-on:: handlers and
    # break without it. Tighten to nonces once the inline blocks are refactored. Tunable via env.
    CONTENT_SECURITY_POLICY = os.environ.get("CONTENT_SECURITY_POLICY", "; ".join([
        "default-src 'self'",
        # static.cloudflareinsights.com: the Web Analytics beacon Cloudflare auto-injects into proxied
        # pages; blocked, it spams the console and no RUM data is collected.
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://www.google.com "
        "https://www.gstatic.com https://www.googletagmanager.com https://static.cloudflareinsights.com",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com",
        "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net",
        "img-src 'self' data: https:",
        # cdn.plot.ly: Plotly geo charts (map/heatmap visuals) fetch their base-map topojson
        # (e.g. world_110m.json) from there at render time; without it every map visual dies with
        # "unexpected error while fetching topojson file".
        "connect-src 'self' https://www.google-analytics.com https://*.google-analytics.com "
        "https://*.analytics.google.com https://www.googletagmanager.com https://cdn.plot.ly",
        "frame-src https://www.google.com https://recaptcha.google.com",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "object-src 'none'",
        "form-action 'self'",
    ]))
    # Add more configuration settings here as the need arises

def configure(app):
    app.config.from_object(Config)
    # Surface the INVITE_JWT_SECRET -> SECRET_KEY fallback: the key-separation property silently
    # doesn't hold until the operator sets the env var, so say so once at startup (prod only --
    # dev routinely runs without it).
    if not os.environ.get("INVITE_JWT_SECRET") and not app.config["DEBUG"]:
        app.logger.warning(
            "INVITE_JWT_SECRET is not set; invite tokens are being signed with SECRET_KEY. "
            "Set a distinct INVITE_JWT_SECRET so a leak of one key doesn't compromise the other.")
    if not os.environ.get("PASSWORD_RESET_JWT_SECRET") and not app.config["DEBUG"]:
        app.logger.warning(
            "PASSWORD_RESET_JWT_SECRET is not set; password reset tokens are being signed with "
            "SECRET_KEY. Set a distinct PASSWORD_RESET_JWT_SECRET so a leak of one key doesn't "
            "compromise the other.")
    # reCAPTCHA disabled outside DEBUG means the login/feedback bot protection is off in prod --
    # surface it loudly (mirrors the INVITE_JWT_SECRET warning above).
    if not app.config["RECAPTCHA_ENABLED"] and not app.config["DEBUG"]:
        app.logger.warning(
            "RECAPTCHA_ENABLED is false while DEBUG is off; login and feedback bot protection is "
            "DISABLED. Set RECAPTCHA_ENABLED=true with real v3 keys in production.")

# Test code below
if __name__ == '__main__':
    pass # Replace this with function calls or test code