import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from data_scraping import orchestrator, registry, storage as storage_mod
from data_scraping.context import ScrapeResult
from data_viz.database import db
from data_viz.database.models import ScrapeRun, SourceSettings, User, UserActivity
from tests import scrape_fixtures as fx
from tests.factories import make_user

D = datetime.date   # brief's tests spell dates as D(y, m, d); datetime is already imported above


def _run(source_key="fakeSource", **kw):
    run = ScrapeRun(source_key=source_key, trigger=kw.pop("trigger", ScrapeRun.TRIGGER_MANUAL),
                    status=kw.pop("status", ScrapeRun.STATUS_PUBLISHED), **kw)
    db.session.add(run)
    db.session.flush()
    return run


class TestModels:
    def test_only_one_active_run_per_source(self, db_session):
        _run(is_active=True)
        with pytest.raises(IntegrityError):
            _run(is_active=True)
        db.session.rollback()

    def test_active_runs_of_different_sources_coexist(self, db_session):
        _run("a", is_active=True)
        _run("b", is_active=True)
        assert ScrapeRun.query.filter_by(is_active=True).count() == 2

    def test_source_settings_default_off(self, db_session):
        s = SourceSettings(source_key="fakeSource")
        db.session.add(s)
        db.session.flush()
        assert s.schedule_enabled is False and s.auto_publish is False
        assert s.consecutive_failures == 0

    def test_user_report_subscription_defaults_false(self, db_session):
        from tests.factories import make_user
        assert make_user().data_report_subscribed is False


# --- Task 7: orchestrator --------------------------------------------------------------------
#
# Deviation from brief: the brief's own module-level helper is also named `_run(env,
# trigger="manual")`, which collides with Task 1's `_run(source_key, **kw)` above (still used by
# TestModels). Renamed the brief's helper to `_scrape_run` throughout; Task 1's `_run` is untouched.


@pytest.fixture()
def fake_env(db_session, tmp_path, monkeypatch):
    spec = fx.fake_spec()
    monkeypatch.setitem(registry.SOURCES, spec.key, spec)
    store = storage_mod.LocalStorage(tmp_path / "bucket")
    rebuilt = []
    monkeypatch.setitem(orchestrator.REBUILDERS, "v1", lambda s, run, st: rebuilt.append(run.id))
    fx.FAKE_SCRAPE_RESULT.clear()
    # TEST HARNESS FIX (not production code): db_session binds each engine to a connection with an
    # already-open outer transaction (SQLAlchemy 2.0's default join_transaction_mode =
    # "conservative_savepoint"). With nothing to nest under, an app-level db.session.rollback() (the
    # orchestrator's exception handlers) rolls back that WHOLE outer transaction, wiping rows the
    # test committed earlier in the same test (e.g. the first published run in
    # test_publish_failure_keeps_previous_active). Opening a SAVEPOINT on each bound connection up
    # front gives the Session's own commit/rollback boundary something to nest under, so an app
    # rollback() only undoes work since the app's own last commit.
    for conn in db.engines.values():
        conn.begin_nested()
    return SimpleNamespace(spec=spec, store=store, rebuilt=rebuilt, tmp=tmp_path,
                           lock=lambda key, ttl: (lambda: None))


def _scrape_returns(tmp, rows, until):
    def result(ctx):
        path = fx.write_csv(f"{ctx.work_dir}/out.csv", rows)
        return ScrapeResult.new_data(path, until, D(2026, 9, 24))
    fx.FAKE_SCRAPE_RESULT["result"] = result


def _scrape_run(env, trigger="manual"):
    return orchestrator.run_source(env.spec.key, trigger, storage=env.store, lock=env.lock)


class TestRunSource:
    def test_first_scrape_held_when_auto_publish_off(self, fake_env):
        _scrape_returns(fake_env.tmp, fx.good_rows(), D(2024, 12, 31))
        run = _scrape_run(fake_env)
        assert run.status == "held" and run.s3_key.endswith("_fakeSource.csv")
        assert fake_env.store.exists(run.s3_key)
        assert fake_env.rebuilt == []

    def test_auto_publish_publishes_and_activates(self, fake_env):
        orchestrator.set_setting("fakeSource", "auto_publish", True, None)
        _scrape_returns(fake_env.tmp, fx.good_rows(), D(2024, 12, 31))
        run = _scrape_run(fake_env)
        assert run.status == "published" and run.is_active and fake_env.rebuilt == [run.id]

    def test_no_new_data_writes_row_without_archive(self, fake_env):
        fx.FAKE_SCRAPE_RESULT["result"] = lambda ctx: ScrapeResult.no_new_data("page date unchanged")
        run = _scrape_run(fake_env)
        assert run.status == "no_new_data" and run.s3_key is None

    def test_identical_rescrape_not_archived_twice(self, fake_env):
        orchestrator.set_setting("fakeSource", "auto_publish", True, None)
        _scrape_returns(fake_env.tmp, fx.good_rows(), D(2024, 12, 31))
        _scrape_run(fake_env)
        again = _scrape_run(fake_env)
        assert again.status == "no_new_data" and again.s3_key is None
        assert len(list((fake_env.tmp / "bucket").rglob("*.csv"))) == 1

    def test_tier1_failure_goes_to_rejected_prefix(self, fake_env):
        _scrape_returns(fake_env.tmp, [{"Region": "Alberta", "Year": "2024"}], D(2024, 12, 31))
        run = _scrape_run(fake_env)
        assert run.status == "failed" and "/rejected/" in run.s3_key

    def test_three_failures_pause_schedule(self, fake_env):
        orchestrator.set_setting("fakeSource", "schedule_enabled", True, None)
        fx.FAKE_SCRAPE_RESULT["raise"] = RuntimeError("layout changed")
        for _ in range(3):
            assert _scrape_run(fake_env, "schedule").status == "failed"
        settings = orchestrator.settings_for("fakeSource")
        assert settings.schedule_enabled is False and "3 consecutive" in settings.paused_reason

    def test_disabled_schedule_is_silent(self, fake_env):
        assert _scrape_run(fake_env, "schedule") is None
        assert ScrapeRun.query.filter_by(source_key="fakeSource").count() == 0

    def test_lock_held_returns_none(self, fake_env):
        assert orchestrator.run_source("fakeSource", "manual", storage=fake_env.store,
                                       lock=lambda k, t: None) is None

    def test_identical_rescrape_is_no_new_data(self, fake_env):
        orchestrator.set_setting("fakeSource", "auto_publish", True, None)
        _scrape_returns(fake_env.tmp, fx.good_rows(), D(2024, 12, 31))
        _scrape_run(fake_env)
        assert _scrape_run(fake_env).status == "no_new_data"

    def test_manual_run_logs_activity_against_the_run(self, fake_env):
        # Fix round 1, item 5c: flush before log_activity so run.id is real, and pass it as target
        # (not None).
        user = make_user(site_admin=True)
        _scrape_returns(fake_env.tmp, fx.good_rows(), D(2024, 12, 31))
        run = orchestrator.run_source(fake_env.spec.key, "manual", user.id,
                                      storage=fake_env.store, lock=fake_env.lock)
        activity = UserActivity.query.filter_by(activity_type="scrape_run_now").one()
        assert activity.activity_target_id == run.id

    def test_run_insert_failure_reraises_original_and_releases_lock(self, fake_env):
        # Fix round 2: if the ScrapeRun insert itself fails (bad FK on triggered_by_user_id here),
        # run.id is never assigned. The old except block did `db.session.get(ScrapeRun, run.id)` ->
        # None -> `_record_failure(None, ...)` -> AttributeError, masking the real IntegrityError and
        # recording nothing. It must now re-raise the original exception, and still release the lock
        # + clean up work_dir via finally.
        released = []
        lock = lambda key, ttl: (lambda: released.append(True))
        _scrape_returns(fake_env.tmp, fx.good_rows(), D(2024, 12, 31))
        with pytest.raises(IntegrityError):
            orchestrator.run_source(fake_env.spec.key, "manual", 999999,
                                    storage=fake_env.store, lock=lock)
        assert released == [True]
        assert ScrapeRun.query.filter_by(source_key="fakeSource").count() == 0


class TestPublish:
    def test_publish_failure_keeps_previous_active(self, fake_env, monkeypatch):
        orchestrator.set_setting("fakeSource", "auto_publish", True, None)
        _scrape_returns(fake_env.tmp, fx.good_rows(), D(2024, 12, 31))
        first = _scrape_run(fake_env)

        def boom(spec, run, st):
            raise RuntimeError("cleaner exploded")
        monkeypatch.setitem(orchestrator.REBUILDERS, "v1", boom)
        _scrape_returns(fake_env.tmp, fx.good_rows(extra_year="2025"), D(2025, 12, 31))
        second = _scrape_run(fake_env)
        assert second.status == "publish_failed" and "cleaner exploded" in second.error
        assert db.session.get(ScrapeRun, first.id).is_active is True
        assert second.is_active is False

    def test_manual_publish_of_held_run_logs_activity(self, fake_env):
        user = make_user(site_admin=True)
        _scrape_returns(fake_env.tmp, fx.good_rows(), D(2024, 12, 31))
        held = _scrape_run(fake_env)
        run = orchestrator.publish_run(held.id, user.id, storage=fake_env.store)
        assert run.status == "published" and run.decided_by_user_id == user.id
        assert UserActivity.query.filter_by(activity_type="scrape_published").count() == 1

    def test_discard(self, fake_env):
        user = make_user(site_admin=True)
        _scrape_returns(fake_env.tmp, fx.good_rows(), D(2024, 12, 31))
        held = _scrape_run(fake_env)
        assert orchestrator.discard_run(held.id, user.id).status == "discarded"
        assert UserActivity.query.filter_by(activity_type="scrape_discarded").count() == 1

    def test_publish_resets_consecutive_failures(self, fake_env):
        settings = orchestrator.settings_for("fakeSource")
        settings.consecutive_failures = 2
        db.session.commit()
        _scrape_returns(fake_env.tmp, fx.good_rows(), D(2024, 12, 31))
        held = _scrape_run(fake_env)
        orchestrator.publish_run(held.id, storage=fake_env.store)
        assert orchestrator.settings_for("fakeSource").consecutive_failures == 0

    def test_publish_survives_reconcile_failure(self, fake_env, monkeypatch):
        # Fix round 1, item 1: reconcile_source_aliases() runs AFTER publish_run's commit, in its
        # own try/except -- a failure there must not undo or relabel an already-live publish.
        _scrape_returns(fake_env.tmp, fx.good_rows(), D(2024, 12, 31))
        held = _scrape_run(fake_env)

        def boom(changed_by=None):
            raise RuntimeError("alias reconcile exploded")
        monkeypatch.setattr("data_viz.auth.auth_helpers.reconcile_source_aliases", boom)
        run = orchestrator.publish_run(held.id, storage=fake_env.store)
        assert run.status == "published" and run.is_active is True

    def test_publish_failure_with_user_logs_activity(self, fake_env, monkeypatch):
        # Fix round 1, item 4: a failed user-initiated publish also logs a UserActivity row.
        user = make_user(site_admin=True)
        _scrape_returns(fake_env.tmp, fx.good_rows(), D(2024, 12, 31))
        held = _scrape_run(fake_env)

        def boom(spec, run, st):
            raise RuntimeError("cleaner exploded")
        monkeypatch.setitem(orchestrator.REBUILDERS, "v1", boom)
        run = orchestrator.publish_run(held.id, user.id, storage=fake_env.store)
        assert run.status == "publish_failed"
        assert UserActivity.query.filter_by(activity_type="scrape_publish_failed").count() == 1


class TestPublishGuards:
    def test_publish_of_rejected_run_raises(self, fake_env):
        run = ScrapeRun(source_key="fakeSource", trigger=ScrapeRun.TRIGGER_UPLOAD,
                        status=ScrapeRun.STATUS_REJECTED)
        db.session.add(run)
        db.session.commit()
        with pytest.raises(ValueError):
            orchestrator.publish_run(run.id, storage=fake_env.store)

    def test_publish_of_discarded_run_raises(self, fake_env):
        run = ScrapeRun(source_key="fakeSource", trigger=ScrapeRun.TRIGGER_UPLOAD,
                        status=ScrapeRun.STATUS_DISCARDED)
        db.session.add(run)
        db.session.commit()
        with pytest.raises(ValueError):
            orchestrator.publish_run(run.id, storage=fake_env.store)


class TestUpload:
    def test_upload_valid_then_processed(self, fake_env):
        user = make_user(site_admin=True)
        path = fx.write_csv(fake_env.tmp / "up.csv", fx.good_rows())
        run = orchestrator.create_upload_run("fakeSource", path, "up.csv", D(2024, 12, 31), user.id, storage=fake_env.store)
        assert run.status == "validating" and "/incoming/" in run.s3_key
        run = orchestrator.process_upload(run.id, storage=fake_env.store)
        assert run.status == "held" and "/scrapes/" in run.s3_key

    def test_upload_invalid_rejected_never_archived(self, fake_env):
        user = make_user(site_admin=True)
        path = fx.write_csv(fake_env.tmp / "up.csv", [{"Region": "Alberta"}])
        run = orchestrator.create_upload_run("fakeSource", path, "up.csv", D(2024, 12, 31), user.id, storage=fake_env.store)
        run = orchestrator.process_upload(run.id, storage=fake_env.store)
        assert run.status == "rejected" and "/rejected/" in run.s3_key
        assert not any("/scrapes/" in str(p) for p in (fake_env.tmp / "bucket").rglob("*"))

    def test_process_upload_of_non_validating_run_is_noop(self, fake_env):
        run = ScrapeRun(source_key="fakeSource", trigger=ScrapeRun.TRIGGER_UPLOAD,
                        status=ScrapeRun.STATUS_HELD)
        db.session.add(run)
        db.session.commit()
        result = orchestrator.process_upload(run.id, storage=fake_env.store)
        assert result.status == "held" and result.id == run.id

    def test_flip_to_running_resets_started_at_so_sweep_cannot_catch_it_mid_processing(self, fake_env, monkeypatch):
        # A validating upload can sit in the single-lane scrape queue past its source's time limit +
        # sweep margin before a worker actually picks it up. process_upload's started_at came from
        # create_upload_run's web-side insert; if it isn't reset the moment the run flips to
        # "running", sweep_stuck_runs (beat, hourly) sees a stale started_at and reaps it as "worker
        # lost" the instant real processing begins. Simulate the concurrent sweep by calling it from
        # inside a tier1 spy, right after the flip's own commit.
        user = make_user(site_admin=True)
        path = fx.write_csv(fake_env.tmp / "up.csv", fx.good_rows())
        run = orchestrator.create_upload_run("fakeSource", path, "up.csv", D(2024, 12, 31), user.id, storage=fake_env.store)
        run.started_at = datetime.datetime(2020, 1, 1)
        db.session.commit()

        from data_scraping import checks
        original_tier1 = checks.tier1
        swept_mid_flight = []

        def spy_tier1(*a, **kw):
            swept_mid_flight.append(orchestrator.sweep_stuck_runs(now=orchestrator._now()))
            return original_tier1(*a, **kw)
        monkeypatch.setattr(checks, "tier1", spy_tier1)

        orchestrator.process_upload(run.id, storage=fake_env.store)
        assert swept_mid_flight == [0]


class TestRollback:
    def test_rollback_reactivates_and_pins(self, fake_env):
        user = make_user(site_admin=True)
        orchestrator.set_setting("fakeSource", "auto_publish", True, None)
        _scrape_returns(fake_env.tmp, fx.good_rows(), D(2024, 12, 31))
        first = _scrape_run(fake_env)
        _scrape_returns(fake_env.tmp, fx.good_rows(extra_year="2025"), D(2025, 12, 31))
        second = _scrape_run(fake_env)
        log_row = orchestrator.rollback_source("fakeSource", first.id, user.id, storage=fake_env.store)
        assert log_row.trigger == "rollback" and log_row.rollback_of_run_id == first.id
        assert db.session.get(ScrapeRun, first.id).is_active and not db.session.get(ScrapeRun, second.id).is_active
        assert orchestrator.settings_for("fakeSource").auto_publish is False
        assert UserActivity.query.filter_by(activity_type="scrape_rollback").count() == 1

    def test_cannot_roll_back_to_unpublished(self, fake_env):
        user = make_user(site_admin=True)
        _scrape_returns(fake_env.tmp, fx.good_rows(), D(2024, 12, 31))
        held = _scrape_run(fake_env)
        with pytest.raises(ValueError):
            orchestrator.rollback_source("fakeSource", held.id, user.id, storage=fake_env.store)

    def test_candidates_exclude_rollback_rows_and_active(self, fake_env):
        user = make_user(site_admin=True)
        orchestrator.set_setting("fakeSource", "auto_publish", True, None)
        _scrape_returns(fake_env.tmp, fx.good_rows(), D(2024, 12, 31))
        first = _scrape_run(fake_env)
        _scrape_returns(fake_env.tmp, fx.good_rows(extra_year="2025"), D(2025, 12, 31))
        second = _scrape_run(fake_env)
        assert [r.id for r in orchestrator.rollback_candidates("fakeSource")] == [first.id]

    def test_rollback_failure_keeps_previous_active_and_logs(self, fake_env, monkeypatch):
        # Symmetric with publish_run's failure path: a failed rollback must not touch is_active (the
        # SAVEPOINT harness makes the app-level rollback() safe here) and, for a user-initiated
        # rollback, logs a scrape_rollback_failed UserActivity alongside the publish_failed log row.
        user = make_user(site_admin=True)
        orchestrator.set_setting("fakeSource", "auto_publish", True, None)
        _scrape_returns(fake_env.tmp, fx.good_rows(), D(2024, 12, 31))
        first = _scrape_run(fake_env)
        _scrape_returns(fake_env.tmp, fx.good_rows(extra_year="2025"), D(2025, 12, 31))
        second = _scrape_run(fake_env)

        def boom(spec, run, st):
            raise RuntimeError("rebuild exploded")
        monkeypatch.setitem(orchestrator.REBUILDERS, "v1", boom)
        log_row = orchestrator.rollback_source("fakeSource", first.id, user.id, storage=fake_env.store)
        assert log_row.status == "publish_failed" and "rebuild exploded" in log_row.error
        assert db.session.get(ScrapeRun, second.id).is_active is True
        assert db.session.get(ScrapeRun, first.id).is_active is False
        assert UserActivity.query.filter_by(activity_type="scrape_rollback_failed").count() == 1

    def test_rollback_failure_before_any_settings_row_leaves_previous_active(self, fake_env, monkeypatch):
        # Review fix round 1: settings_for() commits when it has to CREATE the SourceSettings row. If
        # rollback_source fetched it only after queuing the is_active flips, that implicit commit would
        # persist the flips ahead of the rebuild, making a later rebuild failure unrecoverable. No
        # set_setting()/auto_publish call here on purpose -- the row must not exist yet.
        first = _run(status=ScrapeRun.STATUS_PUBLISHED, is_active=False, data_until=D(2024, 12, 31))
        second = _run(status=ScrapeRun.STATUS_PUBLISHED, is_active=True, data_until=D(2025, 12, 31))
        db.session.commit()
        assert db.session.get(SourceSettings, "fakeSource") is None

        def boom(spec, run, st):
            raise RuntimeError("rebuild exploded")
        monkeypatch.setitem(orchestrator.REBUILDERS, "v1", boom)
        log_row = orchestrator.rollback_source("fakeSource", first.id, None, storage=fake_env.store)
        assert log_row.status == "publish_failed" and "rebuild exploded" in log_row.error
        assert db.session.get(ScrapeRun, second.id).is_active is True
        assert db.session.get(ScrapeRun, first.id).is_active is False


def test_sweep_marks_stuck_runs_failed(fake_env):
    run = ScrapeRun(source_key="fakeSource", trigger="schedule", status="running",
                    started_at=datetime.datetime(2026, 9, 24, 1, 0))
    db.session.add(run)
    db.session.commit()
    assert orchestrator.sweep_stuck_runs(now=datetime.datetime(2026, 9, 24, 3, 0)) == 1
    assert run.status == "failed" and "worker lost" in run.error


def test_sweep_leaves_stale_validating_run_alone(fake_env):
    # Fix round 1, item 3: sweep only ever touches status == running -- a "validating" upload is
    # just queued, not yet claimed by a worker.
    run = ScrapeRun(source_key="fakeSource", trigger=ScrapeRun.TRIGGER_UPLOAD, status="validating",
                    started_at=datetime.datetime(2026, 9, 24, 1, 0))
    db.session.add(run)
    db.session.commit()
    assert orchestrator.sweep_stuck_runs(now=datetime.datetime(2026, 9, 24, 3, 0)) == 0
    assert run.status == "validating"


# --- Task 13: bootstrap-sources ---------------------------------------------------------------
#

class TestBootstrap:
    def test_uploads_newest_as_active_and_is_idempotent(self, fake_env, tmp_path):
        out = tmp_path / "output"; out.mkdir()
        (out / "20260101_20251231_fakeSource.csv").write_text("a\n1\n")
        (out / "20260201_20260131_fakeSource.csv").write_text("a\n2\n")
        (out / "Drug Checking Data Harmonization- Cycle 2.xlsx").write_text("ignored")
        stats = orchestrator.bootstrap_sources(str(out), storage=fake_env.store)
        assert stats == {"uploaded": 2, "skipped": 0}
        runs = ScrapeRun.query.filter_by(source_key="fakeSource").order_by(ScrapeRun.data_until).all()
        assert [r.is_active for r in runs] == [False, True]
        assert all(r.trigger == "bootstrap" and r.status == "published" for r in runs)
        assert orchestrator.bootstrap_sources(str(out), storage=fake_env.store) == {"uploaded": 0, "skipped": 2}


class TestBootstrapSourcesCli:
    """`flask bootstrap-sources` -- the one-time prod runbook step (DEPLOY_LIGHTSAIL.md SS12)."""

    def _run(self, app, *args):
        return app.test_cli_runner().invoke(args=["bootstrap-sources", *args])

    def test_missing_directory_exits_nonzero_with_clear_message_not_a_traceback(self, app, db_session):
        result = self._run(app, "--dir", "/no/such/directory/at/all")
        assert result.exit_code != 0
        assert "directory not found" in result.output.lower()
        assert result.exc_info is None or not issubclass(result.exc_info[0], FileNotFoundError)

    def test_warns_for_every_registered_source_still_missing_an_active_version(self, app, db_session, tmp_path, monkeypatch):
        # An empty (but existing) output/ dir: nothing gets archived, so every registered source --
        # including our fake one -- is still missing an active version afterwards.
        spec = fx.fake_spec()
        monkeypatch.setitem(registry.SOURCES, spec.key, spec)
        monkeypatch.setattr(storage_mod, "get_storage", lambda: storage_mod.LocalStorage(tmp_path / "bucket"))
        out = tmp_path / "output"; out.mkdir()
        result = self._run(app, "--dir", str(out))
        assert result.exit_code == 0
        assert f"WARNING: {spec.key} has no active version" in result.output

    def test_no_warning_once_a_source_has_an_active_version(self, app, db_session, tmp_path, monkeypatch):
        spec = fx.fake_spec()
        monkeypatch.setitem(registry.SOURCES, spec.key, spec)
        monkeypatch.setattr(storage_mod, "get_storage", lambda: storage_mod.LocalStorage(tmp_path / "bucket"))
        out = tmp_path / "output"; out.mkdir()
        (out / "20260101_20251231_fakeSource.csv").write_text("a\n1\n")
        result = self._run(app, "--dir", str(out))
        assert result.exit_code == 0
        assert f"WARNING: {spec.key}" not in result.output


# --- Task 10: nightly report ------------------------------------------------------------------
#
# Deviation from brief: the brief's tests call `_run(fake_env)`, which collides with Task 1's
# `_run(source_key, **kw)` helper already defined above; using `_scrape_run(fake_env)` instead
# (the same rename Task 7 already made for this reason).

class TestReport:
    def test_recipients_are_active_subscribed_site_admins(self, db_session):
        from data_scraping.report import report_recipients
        yes = make_user(site_admin=True); yes.data_report_subscribed = True
        demoted = make_user(site_admin=False); demoted.data_report_subscribed = True
        gone = make_user(site_admin=True, status=User.STATUS_DEACTIVATED); gone.data_report_subscribed = True
        make_user(site_admin=True)   # not subscribed
        db.session.flush()
        assert report_recipients() == [yes.email]

    def test_report_sent_even_when_nothing_ran(self, db_session, monkeypatch):
        from data_scraping import report
        sent = []
        monkeypatch.setattr(report, "send_ses_email", lambda to, subject, html: sent.append((to, subject, html)) or True)
        u = make_user(site_admin=True); u.data_report_subscribed = True
        db.session.flush()
        report.send_nightly_report(now=datetime.datetime(2026, 9, 25, 12, 0))
        assert len(sent) == 1 and "nothing ran" in sent[0][2].lower()

    def test_collect_sections(self, fake_env):
        orchestrator.set_setting("fakeSource", "auto_publish", True, None)
        _scrape_returns(fake_env.tmp, fx.good_rows(), D(2024, 12, 31))
        published = _scrape_run(fake_env)
        fx.FAKE_SCRAPE_RESULT.clear()
        fx.FAKE_SCRAPE_RESULT["raise"] = RuntimeError("boom")
        _scrape_run(fake_env)
        from data_scraping.report import collect_report
        data = collect_report(orchestrator._now() + datetime.timedelta(minutes=1))
        assert len(data["ran"]) == 2
        assert data["updated"][0]["source"] == "Fake Source"
        assert data["issues"][0]["status"] == "failed"

    def test_staleness_flag(self, fake_env):
        run = ScrapeRun(source_key="fakeSource", trigger="upload", status="published", is_active=True,
                        data_until=D(2024, 1, 1), started_at=datetime.datetime(2026, 1, 1), finished_at=datetime.datetime(2026, 1, 1))
        db.session.add(run); db.session.commit()
        from data_scraping.report import collect_report
        status = {row["key"]: row for row in collect_report(datetime.datetime(2026, 9, 24))["status"]}
        assert status["fakeSource"]["stale_days"] > 30 and status["fakeSource"]["stale"]
