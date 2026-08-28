"""Route-test fixtures: no route test may reach AWS, Google, or Redis - the stubs are
autouse here. Individual tests read ses_outbox / celery_stub via the fixtures directly
when they need to assert on captured calls."""
import pytest


@pytest.fixture(autouse=True)
def _route_isolation(ses_outbox, celery_stub):
    yield


@pytest.fixture()
def login_as(client, db_session):
    """Fast login: write Flask-Login's session key directly. The real login form flow
    (recaptcha, bcrypt, lockout) has its own explicit tests in test_auth_routes.py."""
    def _login(user):
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True
        return user
    return _login
