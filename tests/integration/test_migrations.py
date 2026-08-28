"""Migration-chain smoke test: `flask db upgrade` from empty to head on a scratch
database. Runs in a subprocess with its own DATABASE_URL because the in-process app
singleton is already bound to the suite's *_test database."""
import os
import subprocess
import sys

import psycopg2
import pytest
from alembic.script import ScriptDirectory
from sqlalchemy.engine.url import make_url

import data_viz

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(data_viz.__file__)))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "data_viz", "database", "migrations")


def _admin_connection(url):
    conn = psycopg2.connect(host=url.host, port=url.port or 5432, user=url.username,
                            password=url.password, dbname="postgres")
    conn.autocommit = True
    return conn


@pytest.fixture()
def scratch_db_url(db_session):
    """A freshly-dropped-and-created empty database next to the suite's *_test one."""
    base = make_url(os.environ["DATABASE_URL"])
    name = f"{base.database}_migrations"
    assert name.endswith("_test_migrations")
    url = base.set(database=name)
    conn = _admin_connection(base)
    with conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        cur.execute(f'CREATE DATABASE "{name}"')
    conn.close()
    yield url.render_as_string(hide_password=False)
    conn = _admin_connection(base)
    with conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    conn.close()


@pytest.mark.migrations
def test_upgrade_head_on_empty_database(scratch_db_url):
    env = {**os.environ, "DATABASE_URL": scratch_db_url, "FLASK_APP": "data_viz"}
    result = subprocess.run(
        [sys.executable, "-m", "flask", "db", "upgrade"],
        env=env, capture_output=True, text=True, timeout=180, cwd=REPO_ROOT)
    assert result.returncode == 0, f"flask db upgrade failed:\n{result.stderr}"

    expected_head = ScriptDirectory(MIGRATIONS_DIR).get_current_head()
    url = make_url(scratch_db_url)
    conn = psycopg2.connect(host=url.host, port=url.port or 5432, user=url.username,
                            password=url.password, dbname=url.database)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            (stamped,) = cur.fetchone()
        assert stamped == expected_head
    finally:
        conn.close()
