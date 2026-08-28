"""The expire_invite task body, called directly (no broker; eager mode is deliberately
NOT used anywhere - see the celery_stub fixture note in conftest)."""
from celery_worker.tasks.invite_jwt_expiry import expire_invite

from tests.factories import make_invite


def test_expires_pending_invite(db_session):
    invite = make_invite(status="pending")
    result = expire_invite.apply(args=[invite.id]).get()
    assert "expired successfully" in result
    # The task ran under its own (nested) app context and therefore its own
    # Flask-SQLAlchemy session; expire so this session re-reads the committed row.
    db_session.expire_all()
    assert invite.status == "expired"


def test_refuses_non_pending_invite(db_session):
    invite = make_invite(status="accepted")
    result = expire_invite.apply(args=[invite.id]).get()
    assert "already accepted" in result
    assert invite.status == "accepted"


def test_missing_invite_reports_not_found(db_session):
    result = expire_invite.apply(args=[99999999]).get()
    assert "not found" in result
