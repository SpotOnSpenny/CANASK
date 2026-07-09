# Shared server-side reCAPTCHA v3 verification for form-handling routes.
#
# Both the login POST and the feedback POST verify a v3 token here so the "is this a bot"
# decision -- and its fail-closed behavior -- lives in exactly one place. v3 returns a bot
# score (0.0-1.0) and the action the token was minted for, so verification checks all three
# of: success, matching action, and score >= threshold -- not just `success`.

import requests
from flask import current_app

_SITEVERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"


def verify_recaptcha(token, action):
    """Verify a reCAPTCHA v3 token with Google for the given expected action.

    Returns (ok: bool, reason: str). Fails CLOSED -- a missing secret, a transport/parse
    failure, a mismatched action, or a below-threshold score all return (False, ...) with a
    log line; the caller must reject the request. When RECAPTCHA_ENABLED is false (dev),
    returns (True, "disabled") without contacting Google.
    """
    if not current_app.config.get("RECAPTCHA_ENABLED", True):
        return True, "disabled"

    secret = current_app.config.get("RECAPTCHA_SECRET")
    if not secret:
        current_app.logger.error("RECAPTCHA_SECRET is not set; rejecting %s submission", action)
        return False, "not configured"

    if not token:
        return False, "missing token"

    try:
        response = requests.post(
            _SITEVERIFY_URL,
            data={"secret": secret, "response": token},
            timeout=5,
        )
        result = response.json()
    except (requests.RequestException, ValueError):
        # Google unreachable / malformed response -- treat as a verification failure, never
        # fall through to the protected action, but log so an outage is visible to ops.
        current_app.logger.exception("reCAPTCHA verification request to Google failed")
        return False, "verification unavailable"

    if not result.get("success"):
        current_app.logger.warning(
            "reCAPTCHA rejected %s: HTTP %s, error-codes=%s",
            action, response.status_code, result.get("error-codes"))
        return False, "verification failed"

    # A token is minted for a specific action; a mismatch means it was replayed from a
    # different form (or forged), so reject it.
    returned_action = result.get("action")
    if returned_action != action:
        current_app.logger.warning(
            "reCAPTCHA action mismatch on %s: token action=%r", action, returned_action)
        return False, "action mismatch"

    score = result.get("score", 0.0)
    threshold = current_app.config.get("RECAPTCHA_MIN_SCORE", 0.5)
    if score < threshold:
        current_app.logger.warning(
            "reCAPTCHA low score on %s: score=%s < threshold=%s", action, score, threshold)
        return False, "low score"

    return True, "ok"
