"""The expire_invite task body, called directly (no broker; eager mode is deliberately
NOT used anywhere - see the celery_stub fixture note in conftest)."""
import pytest
from celery.schedules import crontab

from celery_worker.tasks.invite_jwt_expiry import expire_invite
from celery_worker.tasks import data_collection
from data_scraping import registry, orchestrator
from data_viz import celery as celery_app

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


# --- Scrape orchestration Celery wiring (thin wrappers around data_scraping.orchestrator) ---

def test_beat_schedule_and_timezone():
    beat = celery_app.conf.beat_schedule
    assert celery_app.conf.timezone == "America/Edmonton"
    assert beat["nightly-refresh"]["schedule"] == crontab(hour=1, minute=0)
    assert beat["nightly-report"]["schedule"] == crontab(hour=6, minute=0)
    assert beat["sweep-stuck-runs"]["schedule"] == crontab(minute=30)


def test_scrape_tasks_route_to_scrape_queue():
    routes = celery_app.conf.task_routes
    for name in ("run_source_task", "process_upload_task", "publish_run_task", "rollback_task"):
        assert routes[f"celery_worker.tasks.data_collection.{name}"] == {"queue": "scrape"}


def test_nightly_refresh_enqueues_only_enabled_automated_sources(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr(data_collection, "enqueue_scrape", lambda key, trigger, user_id=None: sent.append((key, trigger)))
    spec = registry.get_source("nationalHealthInfobase")
    monkeypatch.setattr(registry, "automated_sources", lambda: [spec])
    orchestrator.set_setting(spec.key, "schedule_enabled", True, None)
    data_collection.nightly_refresh()
    assert sent == [("nationalHealthInfobase", "schedule")]


def test_enqueue_scrape_applies_time_limits(monkeypatch):
    calls = []
    monkeypatch.setattr(data_collection.run_source_task, "apply_async",
                        lambda **kw: calls.append(kw))
    data_collection.enqueue_scrape("bcCoronersReport", "manual", 7)
    assert calls[0]["soft_time_limit"] == 1800 and calls[0]["time_limit"] == 1860
    assert calls[0]["args"] == ("bcCoronersReport", "manual", 7)


def test_enqueue_upload_processing_applies_time_limits(monkeypatch):
    calls = []
    monkeypatch.setattr(data_collection.process_upload_task, "apply_async",
                        lambda **kw: calls.append(kw))
    data_collection.enqueue_upload_processing(42, "bcCoronersReport")
    assert calls[0]["soft_time_limit"] == 1800 and calls[0]["time_limit"] == 1860
    assert calls[0]["args"] == (42,)


def test_enqueue_publish_applies_time_limits(monkeypatch):
    calls = []
    monkeypatch.setattr(data_collection.publish_run_task, "apply_async",
                        lambda **kw: calls.append(kw))
    data_collection.enqueue_publish(42, 7, "bcCoronersReport")
    assert calls[0]["soft_time_limit"] == 1800 and calls[0]["time_limit"] == 1860
    assert calls[0]["args"] == (42, 7)


def test_enqueue_rollback_applies_time_limits(monkeypatch):
    calls = []
    monkeypatch.setattr(data_collection.rollback_task, "apply_async",
                        lambda **kw: calls.append(kw))
    data_collection.enqueue_rollback("bcCoronersReport", 42, 7)
    assert calls[0]["soft_time_limit"] == 1800 and calls[0]["time_limit"] == 1860
    assert calls[0]["args"] == ("bcCoronersReport", 42, 7)


# publish_run_task / rollback_task: a ValueError from the orchestrator (stale click -- run no
# longer publishable / bad rollback target) is refused quietly; anything else is a real bug and
# must still crash the worker loudly.

def _raise(exc):
    def _raiser(*args, **kwargs):
        raise exc
    return _raiser


def test_publish_run_task_refuses_on_value_error(monkeypatch):
    monkeypatch.setattr(orchestrator, "publish_run", _raise(ValueError("run 1 is running, not publishable")))
    result = data_collection.publish_run_task(1, 2)
    assert "refused" in result


def test_publish_run_task_propagates_other_errors(monkeypatch):
    monkeypatch.setattr(orchestrator, "publish_run", _raise(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        data_collection.publish_run_task(1, 2)


def test_rollback_task_refuses_on_value_error(monkeypatch):
    monkeypatch.setattr(orchestrator, "rollback_source",
                        _raise(ValueError("rollback target must be an earlier published run of this source")))
    result = data_collection.rollback_task("bcCoronersReport", 1, 2)
    assert "refused" in result


def test_rollback_task_propagates_other_errors(monkeypatch):
    monkeypatch.setattr(orchestrator, "rollback_source", _raise(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        data_collection.rollback_task("bcCoronersReport", 1, 2)


def test_nightly_report_task_calls_send_nightly_report(monkeypatch):
    from data_scraping import report
    monkeypatch.setattr(report, "send_nightly_report", lambda now=None: "sent to 1")
    assert data_collection.nightly_report() == "sent to 1"
