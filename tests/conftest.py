"""Root conftest: environment bootstrap + core fixtures.

ORDERING IS LOAD-BEARING. data_viz has no app factory: importing the package builds the
Flask app, and data_viz.config reads os.environ["SECRET_KEY"] at class-body time. Every
os.environ write below must therefore happen at module top-level, BEFORE any data_viz
import. pytest imports this root conftest before collecting any test module, so this is
the one guaranteed-early hook; no test module may set env itself.
"""
import os
import time

import psycopg2
from psycopg2 import sql
from sqlalchemy.engine.url import make_url


def _test_db_url():
    """Never run tests against a non-_test database.

    Priority: explicit TEST_DATABASE_URL (CI) -> DATABASE_URL with its database renamed
    to <name>_test (local: the test container loads app_config/.env.dev, which points at
    the dev database) -> a bare localhost default (host runs outside compose).
    """
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    base = os.environ.get("DATABASE_URL")
    if base:
        url = make_url(base)
        return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)
    return "postgresql://postgres:postgres@localhost:5432/canask_test"


_TEST_URL = _test_db_url()
assert make_url(_TEST_URL).database.endswith("_test"), (
    f"Refusing to run tests against a non-_test database: {_TEST_URL!r}. "
    "The suite drops and recreates tables; point TEST_DATABASE_URL at a *_test database.")

# Force, not setdefault: .env.dev supplies a DATABASE_URL pointing at the dev database.
os.environ["DATABASE_URL"] = _TEST_URL
# Kill switches for external services (recaptcha.py early-returns; Flask-Limiter never
# touches Redis). Forced for the same reason.
os.environ["RECAPTCHA_ENABLED"] = "false"
os.environ["RATELIMIT_ENABLED"] = "false"
os.environ["COOKIE_SECURE"] = "false"  # the test client speaks plain HTTP
# Force prod-like DEBUG=false: the local test container inherits DEBUG=true from .env.dev
# while CI leaves it unset, and DEBUG now changes behavior (dev invite links go to the log
# instead of SES). Tests of the DEBUG branch monkeypatch app.config["DEBUG"] themselves.
os.environ["DEBUG"] = "false"
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("PUBLIC_BASE_URL", "http://localhost")
# Celery only connects on apply_async, which route tests stub out (see celery_stub).
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")


def _ensure_database(url_str):
    """Idempotently create the test database via the always-present `postgres` admin DB.

    Called lazily from the _database fixture (not at import): the unit tier (`make
    test-fast`) runs with no Postgres at all, and importing the app never connects.
    """
    url = make_url(url_str)
    last_error = None
    for _ in range(5):
        try:
            conn = psycopg2.connect(
                host=url.host, port=url.port or 5432, user=url.username,
                password=url.password, dbname="postgres")
            break
        except psycopg2.OperationalError as exc:  # db container may still be starting
            last_error = exc
            time.sleep(1)
    else:
        raise RuntimeError(f"Could not reach Postgres at {url.host}: {last_error}")
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (url.database,))
            if not cur.fetchone():
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(url.database)))
    finally:
        conn.close()


# --- env is settled; only now is importing the app safe ------------------------------------
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from data_viz import app as flask_app  # noqa: E402
from data_viz.database import db as _db  # noqa: E402


@pytest.fixture(scope="session")
def app():
    """The module-level app singleton with test overrides. Deliberately DB-free so the
    unit tier runs without Postgres; anything touching the database goes through
    db_session (which pulls in _database below)."""
    flask_app.config.update(
        TESTING=True,
        # Hardcoded True in config.py; disabled suite-wide so POSTs don't need tokens.
        # The dedicated CSRF tests re-enable it via monkeypatch.setitem(app.config, ...).
        WTF_CSRF_ENABLED=False,
    )
    return flask_app


@pytest.fixture(scope="session")
def _database(app):
    """Once per session: create the *_test database if missing, build the schema
    (create_all is verified schema-equivalent to the migration head - the migration
    chain itself is exercised by tests/integration/test_migrations.py)."""
    _ensure_database(_TEST_URL)
    with flask_app.app_context():
        _db.create_all()
    yield
    with flask_app.app_context():
        _db.session.remove()
        _db.drop_all()  # safe: the URL was asserted *_test above


@pytest.fixture()
def app_context(app):
    """A fresh app context per test, so flask.g memoization (e.g. accessible_provinces'
    request cache) can never leak between tests. Yields the context object - the client
    fixture needs it to reset g between requests (see below)."""
    with app.app_context() as ctx:
        yield ctx


@pytest.fixture()
def db_session(app, _database, app_context):
    """Transaction-per-test isolation (the documented Flask-SQLAlchemy 3.1 pattern):
    swap each engine for a connection holding an open transaction; SQLAlchemy 2.0's
    default join_transaction_mode turns app-code session.commit() into SAVEPOINT
    release, and the rollback at teardown erases everything the test wrote.

    Escape hatch, should code ever grab a raw connection outside db.session and escape
    the rollback: replace this with a cleaner that TRUNCATEs _db.metadata.sorted_tables
    reversed with CASCADE. Not built until needed - the canary test in
    tests/integration/test_isolation_canary.py guards the assumption.
    """
    engines = _db.engines
    cleanup = []
    for key, engine in list(engines.items()):
        connection = engine.connect()
        transaction = connection.begin()
        engines[key] = connection
        cleanup.append((key, engine, connection, transaction))

    yield _db.session

    _db.session.remove()
    for key, engine, connection, transaction in cleanup:
        transaction.rollback()
        connection.close()
        engines[key] = engine


@pytest.fixture()
def client(app, db_session, app_context):
    """Test client with per-request g isolation.

    Flask only pushes a fresh app context for a request when none is active. Tests hold
    one open (app_context, for db.session access), so client requests REUSE it - and
    flask.g lives on the app context. Flask-Login caches the loaded user on g
    (g._login_user) and visual_query memoizes on g, so without a reset, request 2 in a
    test would inherit request 1's identity and caches (e.g. an anonymous request before
    login_as would pin current_user to anonymous for the rest of the test). Swapping in
    a fresh g before each request restores real per-request semantics while keeping one
    shared SQLAlchemy session (so ORM objects stay attached across requests).
    """
    test_client = app.test_client()
    original_open = test_client.open

    def open_with_fresh_g(*args, **kwargs):
        app_context.g = app.app_ctx_globals_class()
        return original_open(*args, **kwargs)

    test_client.open = open_with_fresh_g
    return test_client


@pytest.fixture()
def ses_outbox(monkeypatch):
    """Capture outbound email. Patches the names BOUND at the call sites (both modules
    do `from data_viz.email import send_ses_email`), not the definition site."""
    sent = []

    def fake_send(to_addresses, subject, html_body):
        sent.append(SimpleNamespace(to=to_addresses, subject=subject, html=html_body))
        return True

    monkeypatch.setattr("data_viz.main.send_ses_email", fake_send)
    monkeypatch.setattr("data_viz.auth.auth.send_ses_email", fake_send)
    return sent


@pytest.fixture()
def celery_stub(monkeypatch):
    """Stub invite-expiry scheduling and revocation.

    Deliberately NOT CELERY_TASK_ALWAYS_EAGER: eager mode ignores eta, so
    expire_invite.apply_async(eta=<72h out>) would run immediately and expire every
    invite the moment it is created.
    """
    record = SimpleNamespace(scheduled=[], revoked=[])

    class _FakeAsyncResult:
        def __init__(self, task_id, *args, **kwargs):
            self.id = task_id

        def revoke(self, *args, **kwargs):
            record.revoked.append(self.id)

    def fake_apply_async(args=None, kwargs=None, eta=None, **_kw):
        record.scheduled.append(SimpleNamespace(args=args, eta=eta))
        return SimpleNamespace(id=f"fake-task-{len(record.scheduled)}")

    monkeypatch.setattr("data_viz.auth.auth.expire_invite",
                        SimpleNamespace(apply_async=fake_apply_async))
    monkeypatch.setattr("data_viz.auth.auth.AsyncResult", _FakeAsyncResult)
    return record
