"""Password reset JWT round trip: generate_jwt -> decode with the configured secret."""
from datetime import timedelta

import jwt
import pytest

from tests.factories import make_invite, make_password_reset, make_user


def secret(app):
    return app.config["PASSWORD_RESET_JWT_SECRET"]


def test_round_trip(app, db_session):
    user = make_user()
    reset = make_password_reset(user)
    payload = jwt.decode(reset.token, secret(app), algorithms=["HS256"])
    assert payload["purpose"] == "password_reset"
    assert payload["user_id"] == user.id
    assert payload["reset_id"] == reset.id
    assert payload["exp"] == pytest.approx(reset.expires_at.timestamp())


def test_expired_reset_token_rejected(app, db_session):
    user = make_user()
    reset = make_password_reset(user, expires_delta=timedelta(seconds=-60))
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(reset.token, secret(app), algorithms=["HS256"])


def test_tampered_token_rejected(app, db_session):
    user = make_user()
    reset = make_password_reset(user)
    with pytest.raises(jwt.InvalidTokenError):
        jwt.decode(reset.token + "x", secret(app), algorithms=["HS256"])
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(reset.token, "wrong-secret", algorithms=["HS256"])


def test_invite_token_lacks_purpose_claim(app, db_session):
    """A token minted by Invites.generate_jwt with the same secret decodes fine (both fall back
    to SECRET_KEY in tests) but carries a different payload shape -- the route relies on the
    missing "purpose" claim to refuse it as a password reset token."""
    invite = make_invite()
    invite_token = invite.generate_jwt(app.config["INVITE_JWT_SECRET"])
    assert app.config["INVITE_JWT_SECRET"] == secret(app)
    payload = jwt.decode(invite_token, secret(app), algorithms=["HS256"])
    assert "purpose" not in payload
    assert "reset_id" not in payload
