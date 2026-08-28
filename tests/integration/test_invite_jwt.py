"""Invite JWT round trip: generate_jwt -> decode with the configured secret."""
import jwt
import pytest

from tests.factories import make_invite


def secret(app):
    return app.config["INVITE_JWT_SECRET"]


def test_round_trip(app, db_session):
    invite = make_invite()
    token = invite.generate_jwt(secret(app))
    payload = jwt.decode(token, secret(app), algorithms=["HS256"])
    assert payload["invite_id"] == invite.id
    assert payload["email"] == invite.email


def test_token_stored_on_row(app, db_session):
    invite = make_invite()
    token = invite.generate_jwt(secret(app))
    assert invite.token == token


def test_expiry_claim_matches_expires_at(app, db_session):
    invite = make_invite()
    token = invite.generate_jwt(secret(app))
    payload = jwt.decode(token, secret(app), algorithms=["HS256"])
    assert payload["exp"] == pytest.approx(invite.expires_at.timestamp())


def test_expired_invite_token_rejected(app, db_session):
    from datetime import timedelta
    invite = make_invite(expires_in=timedelta(seconds=-60))
    token = invite.generate_jwt(secret(app))
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(token, secret(app), algorithms=["HS256"])


def test_tampered_token_rejected(app, db_session):
    invite = make_invite()
    token = invite.generate_jwt(secret(app))
    with pytest.raises(jwt.InvalidTokenError):
        jwt.decode(token + "x", secret(app), algorithms=["HS256"])
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, "wrong-secret", algorithms=["HS256"])
