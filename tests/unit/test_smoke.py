"""Import + liveness smoke: proves the conftest env bootstrap allows the module-level
app singleton to build, and the one DB-free route answers."""


def test_app_imported_with_test_config(app):
    assert app.config["TESTING"] is True
    assert app.config["SQLALCHEMY_DATABASE_URI"].endswith("_test")
    assert app.config["RATELIMIT_ENABLED"] is False
    assert app.config["RECAPTCHA_ENABLED"] is False


def test_healthz(app):
    # /healthz is DB-free and limiter-exempt; use a bare client (no db_session) so this
    # passes even when only the unit tier runs with no database available.
    response = app.test_client().get("/healthz")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "ok"
