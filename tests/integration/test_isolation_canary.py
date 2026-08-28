"""Canary for the db_session rollback pattern: a row committed in one test must be
invisible to the next. If either half fails, the engine-swap fixture in conftest is
broken and the suite is silently leaking state (see the TRUNCATE fallback note there)."""
from data_viz.database import db
from data_viz.database.models import Groups

from tests.factories import make_user

_MARKER = "canary-group-must-not-survive"


def test_canary_writes_and_commits(db_session):
    user = make_user()
    db.session.add(Groups(name=_MARKER, created_by=user.id))
    # A real commit, exactly like app code does mid-request.
    db.session.commit()
    assert Groups.query.filter_by(name=_MARKER).count() == 1


def test_canary_sees_nothing(db_session):
    assert Groups.query.filter_by(name=_MARKER).count() == 0
