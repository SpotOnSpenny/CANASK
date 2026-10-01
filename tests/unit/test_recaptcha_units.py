"""verify_recaptcha fails closed; every rejection must leave a log line so a prod 403 is
diagnosable from the web container logs."""
import logging

from data_viz.recaptcha import verify_recaptcha


def test_missing_token_logs_warning(app, monkeypatch, caplog):
    monkeypatch.setitem(app.config, "RECAPTCHA_ENABLED", True)
    monkeypatch.setitem(app.config, "RECAPTCHA_SECRET", "test-secret")
    with app.app_context(), caplog.at_level(logging.WARNING, logger=app.logger.name):
        assert verify_recaptcha(None, "feedback") == (False, "missing token")
        assert verify_recaptcha("", "feedback") == (False, "missing token")
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("missing" in m and "feedback" in m for m in messages)
