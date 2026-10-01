"""send_ses_email: the SES MessageId is the only handle for tracing a message in the SES console,
so an accepted send must be logged at INFO with it."""
import logging

import pytest
from botocore.exceptions import ClientError

import data_viz.email as email_module
from data_viz.email import send_ses_email, send_ses_email_result


class _FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def send_email(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def _patch_client(monkeypatch, fake):
    monkeypatch.setenv("SES_SENDER_EMAIL", "noreply@example.org")
    monkeypatch.setattr(email_module.boto3, "client", lambda *a, **k: fake)


def test_success_logs_message_id(app, monkeypatch, caplog):
    fake = _FakeClient(response={"MessageId": "abc-123"})
    _patch_client(monkeypatch, fake)
    with app.app_context(), caplog.at_level(logging.INFO, logger=app.logger.name):
        assert send_ses_email(["inbox@example.org"], "Subject", "<p>hi</p>") is True
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("abc-123" in m and "inbox@example.org" in m for m in infos)
    assert fake.calls[0]["Destination"] == {"ToAddresses": ["inbox@example.org"]}


def test_client_error_returns_false_and_logs(app, monkeypatch, caplog):
    error = ClientError({"Error": {"Code": "MessageRejected", "Message": "not verified"}}, "SendEmail")
    _patch_client(monkeypatch, _FakeClient(error=error))
    with app.app_context(), caplog.at_level(logging.ERROR, logger=app.logger.name):
        assert send_ses_email(["inbox@example.org"], "Subject", "<p>hi</p>") is False
    assert any("MessageRejected" in r.getMessage() for r in caplog.records)


class TestSendResult:
    """send_ses_email_result also returns the failure reason, which the feedback inbox persists so
    an admin can tell a config outage from a one-off rejection without reading the log."""

    def test_success(self, app, monkeypatch):
        _patch_client(monkeypatch, _FakeClient(response={"MessageId": "abc-123"}))
        with app.app_context():
            assert send_ses_email_result(["inbox@example.org"], "S", "<p>hi</p>") == (True, None)

    def test_client_error_reason_is_code_and_message(self, app, monkeypatch):
        error = ClientError({"Error": {"Code": "MessageRejected", "Message": "not verified"}}, "SendEmail")
        _patch_client(monkeypatch, _FakeClient(error=error))
        with app.app_context():
            assert send_ses_email_result(["inbox@example.org"], "S", "<p>hi</p>") == \
                (False, "MessageRejected: not verified")

    def test_botocore_error_reason_is_the_class(self, app, monkeypatch):
        from botocore.exceptions import EndpointConnectionError
        error = EndpointConnectionError(endpoint_url="https://email.example")
        _patch_client(monkeypatch, _FakeClient(error=error))
        with app.app_context():
            ok, reason = send_ses_email_result(["inbox@example.org"], "S", "<p>hi</p>")
        assert ok is False and reason.startswith("EndpointConnectionError: ")

    def test_missing_sender_reason(self, app, monkeypatch):
        monkeypatch.delenv("SES_SENDER_EMAIL", raising=False)
        with app.app_context():
            assert send_ses_email_result(["inbox@example.org"], "S", "<p>hi</p>") == \
                (False, "SES_SENDER_EMAIL is not set")

    def test_programming_errors_still_propagate(self, app, monkeypatch):
        _patch_client(monkeypatch, _FakeClient(error=TypeError("bad arg")))
        with app.app_context(), pytest.raises(TypeError):
            send_ses_email_result(["inbox@example.org"], "S", "<p>hi</p>")
