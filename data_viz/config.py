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
    # Add more configuration settings here as the need arises

def configure(app):
    app.config.from_object(Config)

# Test code below
if __name__ == '__main__':
    pass # Replace this with function calls or test code