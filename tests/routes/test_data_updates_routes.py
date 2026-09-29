import datetime as dt
import io
import re

import pytest

from data_scraping import orchestrator, registry
from data_viz.database import db
from data_viz.database.models import ScrapeRun, UserActivity
from tests.factories import make_user, make_group, add_membership, make_data_source, grant_data_source, make_visual

PAGE = "/v1/admin/data-updates"
HX = {"HX-Request": "true"}


def _run(key="bcDrugSense", status="failed", error="Traceback: secret"):
    run = ScrapeRun(source_key=key, trigger="schedule", status=status, error=error)
    db.session.add(run); db.session.flush()
    return run


@pytest.fixture()
def owner(db_session):
    source = make_data_source(name=registry.get_source("bcDrugSense").data_source_name)
    make_visual(province="british-columbia", data_source=source)
    group = make_group()
    grant_data_source(group, source)
    user = make_user()
    add_membership(user, group, role="Data Owner")
    return user


class TestAccess:
    def test_anonymous_redirected_to_login(self, client):
        assert client.get(PAGE).status_code == 302

    def test_viewer_bounced(self, client, login_as):
        login_as(make_user())
        response = client.get(PAGE)
        assert response.status_code == 302 and response.location.endswith("/")

    def test_site_admin_sees_all_sources_and_controls(self, client, login_as):
        login_as(make_user(site_admin=True))
        html = client.get(PAGE, headers=HX).get_data(as_text=True)
        for spec in registry.SOURCES.values():
            assert spec.label in html
        assert 'name="field" value="auto_publish"' in html

    def test_data_owner_sees_only_owned_sources_readonly(self, client, login_as, owner):
        _run()
        login_as(owner)
        html = client.get(PAGE, headers=HX).get_data(as_text=True)
        assert registry.get_source("bcDrugSense").label in html
        assert registry.get_source("onODPRN").label not in html
        assert 'name="field"' not in html
        assert "Traceback: secret" not in client.get(f"{PAGE}/runs", headers=HX).get_data(as_text=True)

    def test_data_owner_cannot_toggle(self, client, login_as, owner):
        login_as(owner)
        r = client.post(f"{PAGE}/sources/bcDrugSense/settings", data={"field": "auto_publish", "value": "1"}, headers=HX)
        assert r.status_code == 403


class TestToggles:
    def test_toggle_updates_setting_and_logs(self, client, login_as):
        admin = login_as(make_user(site_admin=True))
        r = client.post(f"{PAGE}/sources/drugChecking/settings", data={"field": "auto_publish", "value": "1"}, headers=HX)
        assert r.status_code == 200
        assert orchestrator.settings_for("drugChecking").auto_publish is True
        assert UserActivity.query.filter_by(user_id=admin.id, activity_type="source_setting_changed").count() == 1

    def test_unknown_field_400(self, client, login_as):
        login_as(make_user(site_admin=True))
        r = client.post(f"{PAGE}/sources/drugChecking/settings", data={"field": "is_active", "value": "1"}, headers=HX)
        assert r.status_code == 400

    def test_unknown_source_404(self, client, login_as):
        login_as(make_user(site_admin=True))
        assert client.post(f"{PAGE}/sources/nope/settings", data={"field": "auto_publish", "value": "1"}).status_code == 404


class TestSubscription:
    def test_site_admin_can_subscribe(self, client, login_as):
        admin = login_as(make_user(site_admin=True))
        r = client.post(f"{PAGE}/subscription", data={"subscribed": "1"}, headers=HX)
        # 200, not 204: the after_request hook's OOB flash-message injection skips 204/205
        # responses, so a 204 here would silently drop the flash until the next navigation.
        assert r.status_code == 200
        assert "flashed-messages-container" in r.get_data(as_text=True)
        assert admin.data_report_subscribed is True
        assert UserActivity.query.filter_by(activity_type="data_report_subscription").count() == 1

    def test_owner_cannot_subscribe(self, client, login_as, owner):
        login_as(owner)
        assert client.post(f"{PAGE}/subscription", data={"subscribed": "1"}, headers=HX).status_code == 403


def test_running_row_polls_and_finished_row_does_not(client, login_as):
    login_as(make_user(site_admin=True))
    running = _run(status="running", error=None)
    done = _run(status="published", error=None)
    assert 'hx-trigger="every 5s"' in client.get(f"{PAGE}/runs/{running.id}", headers=HX).get_data(as_text=True)
    assert 'hx-trigger="every 5s"' not in client.get(f"{PAGE}/runs/{done.id}", headers=HX).get_data(as_text=True)


def test_nav_link_only_for_visible_users(client, login_as, owner):
    login_as(make_user())
    assert PAGE not in client.get("/", headers=HX).get_data(as_text=True)


@pytest.fixture()
def queued(monkeypatch):
    calls = []
    monkeypatch.setattr("data_viz.data_updates.enqueue_scrape",
                        lambda key, trigger, user_id=None: calls.append(("scrape", key, trigger, user_id)))
    monkeypatch.setattr("data_viz.data_updates.enqueue_upload_processing",
                        lambda run_id, key: calls.append(("process_upload_task", run_id)))
    monkeypatch.setattr("data_viz.data_updates.enqueue_publish",
                        lambda run_id, user_id, key: calls.append(("publish_run_task", run_id, user_id)))
    monkeypatch.setattr("data_viz.data_updates.enqueue_rollback",
                        lambda key, run_id, user_id: calls.append(("rollback_task", key, run_id, user_id)))
    return calls


@pytest.fixture()
def local_store(tmp_path, monkeypatch):
    from data_scraping import storage
    store = storage.LocalStorage(tmp_path / "bucket")
    monkeypatch.setattr("data_viz.data_updates.get_storage", lambda: store)
    return store


class TestRunNow:
    def test_enqueues_manual_scrape(self, client, login_as, queued, monkeypatch):
        # No real registry.SOURCES entry is currently both scheduled-scrape-capable AND onboarded
        # (nationalHealthInfobase has a scrape callable but contract=None until its rollout task
        # lands a FileContract) -- use the fake onboarded+scrape spec tests/scrape_fixtures.py and
        # tests/integration/test_scrape_orchestrator_db.py already share for exactly this shape.
        from tests import scrape_fixtures as fx
        spec = fx.fake_spec()
        monkeypatch.setitem(registry.SOURCES, spec.key, spec)
        admin = login_as(make_user(site_admin=True))
        r = client.post(f"{PAGE}/sources/{spec.key}/run", headers=HX)
        assert r.status_code == 200
        assert queued == [("scrape", spec.key, "manual", admin.id)]

    def test_refuses_not_onboarded_or_upload_only(self, client, login_as, queued):
        login_as(make_user(site_admin=True))
        assert client.post(f"{PAGE}/sources/drugChecking/run", headers=HX).status_code == 400
        assert queued == []


class TestUpload:
    def _post(self, client, key="drugChecking", name="20260908_x.xlsx", body=b"PK\x03\x04data", until="2026-08-13"):
        return client.post(f"{PAGE}/sources/{key}/upload", headers=HX, content_type="multipart/form-data",
                           data={"file": (io.BytesIO(body), name), "data_until": until})

    def test_valid_upload_creates_validating_run_and_enqueues(self, client, login_as, queued, local_store):
        login_as(make_user(site_admin=True))
        r = self._post(client)
        assert r.status_code == 200
        run = ScrapeRun.query.filter_by(source_key="drugChecking").one()
        assert run.status == "validating" and "/incoming/" in run.s3_key and local_store.exists(run.s3_key)
        assert queued == [("process_upload_task", run.id)]

    def test_wrong_extension_rejected_in_modal(self, client, login_as, queued, local_store):
        # 200, not 422: the vendored htmx 1.9.12 doesn't swap non-2xx responses, so a validation
        # error must come back 200 with HX-Retarget/HX-Reswap pointed at the modal body, or the form's
        # own hx-target="#run-log-body" would just... not update, and the error is never seen.
        login_as(make_user(site_admin=True))
        r = self._post(client, name="data.csv")
        assert r.status_code == 200 and ".xlsx" in r.get_data(as_text=True)
        assert r.headers["HX-Retarget"] == "#data-updates-modal-content"
        assert r.headers["HX-Reswap"] == "innerHTML"
        assert ScrapeRun.query.count() == 0 and queued == []

    def test_missing_date_rejected_in_modal(self, client, login_as, queued, local_store):
        login_as(make_user(site_admin=True))
        r = self._post(client, until="")
        assert r.status_code == 200 and r.headers["HX-Retarget"] == "#data-updates-modal-content"

    def test_too_large_rejected_in_modal(self, client, login_as, queued, local_store, app, monkeypatch):
        monkeypatch.setitem(app.config, "SCRAPE_UPLOAD_MAX_BYTES", 10)
        login_as(make_user(site_admin=True))
        r = self._post(client, body=b"x" * 100)
        assert r.status_code == 200 and r.headers["HX-Retarget"] == "#data-updates-modal-content"
        assert "MB upload limit" in r.get_data(as_text=True)
        assert ScrapeRun.query.count() == 0 and queued == []

    def test_storage_failure_shown_in_modal_not_swallowed(self, client, login_as, queued, local_store, monkeypatch, caplog):
        # create_upload_run's own store.put() can fail (e.g. a transient S3 error); it must surface
        # as an in-modal error and be logged, never swallowed as a silent 500 or empty response.
        # The route really calls db.session.rollback(). Under db_session's outer transaction that
        # would wipe the fixture user, so nest the test under a SAVEPOINT first (same harness as
        # fake_env in tests/integration/test_scrape_orchestrator_db.py).
        for conn in db.engines.values():
            conn.begin_nested()
        login_as(make_user(site_admin=True))
        db.session.commit()  # factories only flush; commit (= release the savepoint) so the route's rollback keeps the user

        def boom(*a, **kw):
            raise RuntimeError("put failed")
        monkeypatch.setattr("data_viz.data_updates.orchestrator.create_upload_run", boom)
        with caplog.at_level("ERROR"):
            r = self._post(client)
        assert r.status_code == 200 and r.headers["HX-Retarget"] == "#data-updates-modal-content"
        assert "Upload failed" in r.get_data(as_text=True)
        assert ScrapeRun.query.count() == 0 and queued == []
        assert any("upload of" in rec.getMessage() for rec in caplog.records)

    def test_storage_failure_rolls_back_the_session(self, client, login_as, queued, local_store, monkeypatch):
        # The failed upload's flushed ScrapeRun (or a broken transaction, if the failure was a DB
        # error) must be rolled back before the error modal renders -- its context processors query.
        login_as(make_user(site_admin=True))
        rollbacks = []

        def boom(*a, **kw):
            raise RuntimeError("put failed")
        monkeypatch.setattr("data_viz.data_updates.orchestrator.create_upload_run", boom)
        # Recorder only: a real rollback here would unwind the test's own outer transaction.
        monkeypatch.setattr(db.session, "rollback", lambda: rollbacks.append(True))
        r = self._post(client)
        assert r.status_code == 200 and "Upload failed" in r.get_data(as_text=True)
        assert rollbacks == [True]

    def test_owner_cannot_upload(self, client, login_as, owner, queued, local_store):
        login_as(owner)
        assert self._post(client, key="bcDrugSense", name="a.csv").status_code == 403


class TestUploadSizeCapWithCsrf:
    """Regression coverage for the upload cap being dead under CSRF: CSRFProtect's own
    before_request reads request.form (looking for the csrf_token field, even though this test sends
    the token via header) BEFORE the view runs, so the per-request cap must already be raised by then.
    See the _raise_upload_content_length hook in data_viz/__init__.py, registered ahead of
    csrf.init_app(app)."""

    def _csrf_token(self, client):
        page = client.get("/").get_data(as_text=True)   # full page carries the meta tag
        return re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)

    def _post(self, client, token, body, name="20260908_x.xlsx", until="2026-08-13"):
        return client.post(f"{PAGE}/sources/drugChecking/upload", headers={**HX, "X-CSRFToken": token},
                           content_type="multipart/form-data",
                           data={"file": (io.BytesIO(body), name), "data_until": until})

    def test_upload_over_2mb_default_cap_but_under_scrape_cap_is_accepted(
            self, client, login_as, queued, local_store, app, monkeypatch):
        monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
        monkeypatch.setitem(app.config, "SCRAPE_UPLOAD_MAX_BYTES", 25 * 1024 * 1024)
        login_as(make_user(site_admin=True))
        token = self._csrf_token(client)
        body = b"x" * (3 * 1024 * 1024)   # > MAX_CONTENT_LENGTH (2 MB), < SCRAPE_UPLOAD_MAX_BYTES (25 MB)
        r = self._post(client, token, body)
        assert r.status_code == 200
        assert ScrapeRun.query.filter_by(source_key="drugChecking").count() == 1

    def test_upload_over_scrape_cap_is_rejected_in_modal_not_500(
            self, client, login_as, queued, local_store, app, monkeypatch):
        monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
        monkeypatch.setitem(app.config, "SCRAPE_UPLOAD_MAX_BYTES", 3 * 1024 * 1024)
        login_as(make_user(site_admin=True))
        token = self._csrf_token(client)
        body = b"x" * (4 * 1024 * 1024)   # > the (lowered) SCRAPE_UPLOAD_MAX_BYTES cap
        r = self._post(client, token, body)
        assert r.status_code == 200 and r.headers["HX-Retarget"] == "#data-updates-modal-content"
        assert "MB upload limit" in r.get_data(as_text=True)
        assert ScrapeRun.query.count() == 0

    # The raised cap is only for site admins: the hook runs before @require_auth, so without the gate
    # any client (anonymous included -- CSRF tokens come from any page) could make the app parse
    # 25 MB bodies, and the 413 handler would render the upload modal to them.
    def test_non_admin_gets_default_cap_and_plain_413(self, client, login_as, queued, local_store, app, monkeypatch):
        monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
        monkeypatch.setitem(app.config, "SCRAPE_UPLOAD_MAX_BYTES", 25 * 1024 * 1024)
        login_as(make_user())
        token = self._csrf_token(client)
        r = self._post(client, token, b"x" * (3 * 1024 * 1024))
        assert r.status_code == 413 and "HX-Retarget" not in r.headers
        assert ScrapeRun.query.count() == 0

    def test_anonymous_gets_default_cap_and_plain_413(self, client, queued, local_store, app, monkeypatch):
        monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
        monkeypatch.setitem(app.config, "SCRAPE_UPLOAD_MAX_BYTES", 25 * 1024 * 1024)
        token = self._csrf_token(client)
        r = self._post(client, token, b"x" * (3 * 1024 * 1024))
        assert r.status_code == 413 and "HX-Retarget" not in r.headers
        assert ScrapeRun.query.count() == 0


class TestDecisions:
    def test_publish_and_discard_enqueue_only_for_held(self, client, login_as, queued):
        admin = login_as(make_user(site_admin=True))
        held = _run(status="held", error=None)
        assert client.post(f"{PAGE}/runs/{held.id}/publish", headers=HX).status_code == 200
        assert queued == [("publish_run_task", held.id, admin.id)]
        done = _run(status="published", error=None)
        assert client.post(f"{PAGE}/runs/{done.id}/publish", headers=HX).status_code == 409

    def test_discard_is_synchronous(self, client, login_as, queued):
        login_as(make_user(site_admin=True))
        held = _run(status="held", error=None)
        client.post(f"{PAGE}/runs/{held.id}/discard", headers=HX)
        assert held.status == "discarded"

    def test_rollback_requires_candidate(self, client, login_as, queued):
        admin = login_as(make_user(site_admin=True))
        old = _run(status="published", error=None)
        _run(status="published", error=None).is_active = True
        db.session.flush()
        assert client.post(f"{PAGE}/runs/{old.id}/rollback", headers=HX).status_code == 200
        assert queued == [("rollback_task", "bcDrugSense", old.id, admin.id)]
        failed = _run(status="failed")
        assert client.post(f"{PAGE}/runs/{failed.id}/rollback", headers=HX).status_code == 409

    def test_confirm_modal_names_targets(self, client, login_as):
        login_as(make_user(site_admin=True))
        held = _run(status="held", error=None)
        html = client.get(f"{PAGE}/runs/{held.id}/confirm/publish", headers=HX).get_data(as_text=True)
        assert "british-columbia" in html
