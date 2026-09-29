# The scrape pipeline's state machine. Celery tasks (celery_worker/tasks/data_collection.py) and the
# Data Updates routes are thin wrappers around these functions, which own every ScrapeRun transition.
#
#   run_source:     lock -> scrape -> tier 1 -> tier 2 -> archive -> publish | hold
#   create_upload_run/process_upload: incoming/ -> tier 1 -> scrapes/ -> tier 2 -> publish | hold
#   publish_run:    flip is_active + rebuild in ONE transaction (the FactWriter/DAS ingest commit is
#                   the commit); any failure rolls the flip back with it, so the site keeps serving
#                   the previous version.

import datetime
import logging
import os
import re
import shutil
import tempfile
import traceback

from data_scraping import checks, registry, storage as storage_mod
from data_scraping.context import ActiveMeta, ScrapeContext
from data_viz.database import db
from data_viz.database.models import DataSources, ScrapeRun, SourceSettings, UserActivity

logger = logging.getLogger(__name__)

FAILURE_PAUSE_THRESHOLD = 3
SWEEP_MARGIN = datetime.timedelta(minutes=15)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def log_activity(user_id, activity_type, target_id, details):
    if user_id is None:
        return
    db.session.add(UserActivity(user_id=user_id, activity_type=activity_type,
                                activity_target_type="scrape_run", activity_target_id=target_id,
                                details=details[:5000]))


def settings_for(key):
    settings = db.session.get(SourceSettings, key)
    if settings is None:
        settings = SourceSettings(source_key=key)
        db.session.add(settings)
        db.session.commit()
    return settings


def set_setting(key, field, value, user_id):
    if field not in ("schedule_enabled", "auto_publish"):
        raise ValueError(field)
    settings = settings_for(key)
    setattr(settings, field, bool(value))
    if field == "schedule_enabled" and value:
        settings.paused_reason = None
        settings.consecutive_failures = 0
    settings.updated_by_user_id = user_id
    settings.updated_at = _now()
    log_activity(user_id, "source_setting_changed", None, f"{key}: {field} -> {bool(value)}")
    db.session.commit()
    return settings


def active_run(key):
    return ScrapeRun.query.filter_by(source_key=key, is_active=True).first()


def _data_source_id(spec):
    row = DataSources.query.filter_by(name=spec.data_source_name).first()
    return row.id if row else None


def _default_lock(key, ttl):
    import redis
    client = redis.Redis.from_url(os.environ["CELERY_BROKER_URL"])
    name = f"scrape-lock:{key}"
    if not client.set(name, "1", nx=True, ex=ttl):
        return None
    return lambda: client.delete(name)


def _record_failure(run, message, settings, counts=True):
    run.status = ScrapeRun.STATUS_FAILED
    run.error = message
    run.finished_at = _now()
    if counts:
        settings.consecutive_failures += 1
        if settings.consecutive_failures >= FAILURE_PAUSE_THRESHOLD and settings.schedule_enabled:
            settings.schedule_enabled = False
            settings.paused_reason = f"{FAILURE_PAUSE_THRESHOLD} consecutive failures"
    db.session.commit()


def _materialize(store, key, work_dir):
    path = os.path.join(work_dir, storage_mod.materialized_name(key))
    store.get_to(key, path)
    return path


def _active_version(store, key, work_dir):
    current = active_run(key)
    if current is None or not current.s3_key:
        return current, None
    active_dir = os.path.join(work_dir, "active")
    os.makedirs(active_dir, exist_ok=True)
    path = _materialize(store, current.s3_key, active_dir)
    return current, checks.ActiveVersion(path=path, data_until=current.data_until, content_hash=current.content_hash)


def _archive(store, run, path, area):
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    key = storage_mod.object_key(area, run.source_key, run.id, run.scraped_on, run.data_until, ext)
    store.put(path, key)
    return key


def _decide(spec, run, path, work_dir, store, settings):
    """Shared tail of scrape + upload once tier 1 has passed: tier 2 FIRST (content identical to the
    active version is no_new_data and is never archived), then archive to scrapes/, then hold/publish."""
    current, active = _active_version(store, spec.key, work_dir)
    outcome = checks.tier2(spec, path, run.data_until, run.content_hash, active)
    run.check_results = (run.check_results or []) + [r.to_dict() for r in outcome.results]
    run.previous_data_until = current.data_until if current else None
    if outcome.decision == "no_new_data":
        run.status = ScrapeRun.STATUS_NO_NEW_DATA
        run.finished_at = _now()
        db.session.commit()
        return run
    run.s3_key = _archive(store, run, path, "scrapes")
    db.session.commit()
    if outcome.decision == "hold" or not settings.auto_publish:
        run.status = ScrapeRun.STATUS_HELD
        run.finished_at = _now()
        db.session.commit()
        return run
    return publish_run(run.id, storage=store)


def run_source(key, trigger, user_id=None, *, storage=None, lock=None):
    spec = registry.get_source(key)
    settings = settings_for(key)
    if trigger == ScrapeRun.TRIGGER_SCHEDULE and not settings.schedule_enabled:
        return None
    release = (lock or _default_lock)(key, spec.time_limit + 300)
    if release is None:
        return None
    run = None
    work_dir = None
    try:
        store = storage or storage_mod.get_storage()
        work_dir = tempfile.mkdtemp(prefix=f"scrape-{key}-")
        run = ScrapeRun(source_key=key, trigger=trigger, status=ScrapeRun.STATUS_RUNNING,
                        triggered_by_user_id=user_id, data_source_id=_data_source_id(spec))
        db.session.add(run)
        db.session.flush()
        if trigger == ScrapeRun.TRIGGER_MANUAL:
            log_activity(user_id, "scrape_run_now", run.id, f"Run now: {key}")
        db.session.commit()
        current = active_run(key)
        ctx = ScrapeContext(work_dir, ActiveMeta(current.data_until, current.scraped_on, current.s3_key) if current else None, None)
        try:
            result = registry.resolve_scrape(spec)(ctx)
        finally:
            ctx.close()
        if result.kind == "no_new_data":
            run.status = ScrapeRun.STATUS_NO_NEW_DATA
            run.error = None
            run.check_results = [{"check": "source", "level": "pass", "message": result.reason}]
            run.finished_at = _now()
            settings.consecutive_failures = 0
            db.session.commit()
            return run
        run.data_until, run.scraped_on = result.data_until, result.scraped_on
        run.content_hash = checks.file_hash(result.path)
        tier1 = checks.tier1(result.path, spec.contract)
        run.check_results = [r.to_dict() for r in tier1.results if r.level != "notice"]
        run.notices = [r.to_dict() for r in tier1.results if r.level == "notice"] or None
        if not tier1.ok:
            run.s3_key = _archive(store, run, result.path, "rejected")
            _record_failure(run, "file failed structure checks", settings)
            return run
        settings.consecutive_failures = 0
        db.session.commit()
        return _decide(spec, run, result.path, work_dir, store, settings)
    except Exception:
        # Capture the id BEFORE rollback: rollback() expires/detaches `run`, and if the run row
        # itself never made it into the DB (e.g. the insert's own flush/commit raised -- a bad FK on
        # triggered_by_user_id, say), `run.id` is None and there's nothing to record a failure
        # against. In that case re-raise the ORIGINAL exception rather than masking it behind an
        # AttributeError from _record_failure(None, ...); the lock/work_dir cleanup below still runs.
        run_id = run.id if run is not None else None
        db.session.rollback()
        if run_id is None:
            raise
        run = db.session.get(ScrapeRun, run_id)
        if run is None:
            raise
        _record_failure(run, traceback.format_exc(), settings_for(key))
        return run
    finally:
        release()
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


def create_upload_run(key, file_path, original_filename, data_until, user_id, *, storage=None):
    spec = registry.get_source(key)
    store = storage or storage_mod.get_storage()
    run = ScrapeRun(source_key=key, trigger=ScrapeRun.TRIGGER_UPLOAD, status=ScrapeRun.STATUS_VALIDATING,
                    triggered_by_user_id=user_id, original_filename=original_filename[:255],
                    data_until=data_until, scraped_on=datetime.date.today(),
                    content_hash=checks.file_hash(file_path), data_source_id=_data_source_id(spec))
    db.session.add(run)
    db.session.flush()
    run.s3_key = _archive(store, run, file_path, "incoming")
    log_activity(user_id, "scrape_upload", run.id, f"Uploaded {original_filename} for {key} (data until {data_until})")
    db.session.commit()
    return run


def process_upload(run_id, *, storage=None):
    run = db.session.get(ScrapeRun, run_id)
    if run.status != ScrapeRun.STATUS_VALIDATING:
        return run   # already picked up (or past validating) -- no-op
    spec = registry.get_source(run.source_key)
    store = storage or storage_mod.get_storage()
    # Flip to running (and commit) before doing any work, so sweep_stuck_runs -- which only sweeps
    # status == running -- catches a worker that dies mid-validation, while a queued-but-untouched
    # upload (still "validating") is left alone. started_at must move to now in the SAME commit: it
    # was set at the web insert (create_upload_run), and the single-lane scrape queue can leave an
    # upload "validating" for a while before a worker picks it up -- without this reset, a run whose
    # queue wait already exceeded its time limit + margin would be swept as "worker lost" the instant
    # it actually starts running.
    run.status = ScrapeRun.STATUS_RUNNING
    run.started_at = _now()
    db.session.commit()
    work_dir = tempfile.mkdtemp(prefix=f"upload-{run.source_key}-")
    try:
        path = _materialize(store, run.s3_key, work_dir)
        tier1 = checks.tier1(path, spec.contract)
        run.check_results = [r.to_dict() for r in tier1.results if r.level != "notice"]
        run.notices = [r.to_dict() for r in tier1.results if r.level == "notice"] or None
        if not tier1.ok:
            run.s3_key = _archive(store, run, path, "rejected")
            run.status = ScrapeRun.STATUS_REJECTED
            run.finished_at = _now()
            db.session.commit()
            return run
        db.session.commit()
        return _decide(spec, run, path, work_dir, store, settings_for(run.source_key))   # archives to scrapes/
    except Exception:
        db.session.rollback()
        run = db.session.get(ScrapeRun, run_id)
        run.status = ScrapeRun.STATUS_FAILED
        run.error = traceback.format_exc()
        run.finished_at = _now()
        db.session.commit()
        return run
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _rebuild_v1(spec, run, store):
    # reconcile_source_aliases() deliberately does NOT run here: this function runs inside
    # publish_run's main try, whose except marks the run publish_failed and rolls back the flip.
    # If reconcile ran here, after export_data_to_db()'s own commit, a reconcile failure would roll
    # back only itself (SQLAlchemy has nothing left to undo) while publish_run's except would still
    # mark an already-live publish as publish_failed. publish_run calls reconcile separately, after
    # this function's commit has succeeded, in its own try/except that never touches run.status.
    from data_viz import generate_visuals as gv
    targets = registry.rebuild_targets(spec)
    work_dir = tempfile.mkdtemp(prefix=f"rebuild-{spec.key}-")
    try:
        for source in gv.sources_for_targets(targets):
            version = run if source == spec.key else active_run(source)
            if version is None or not version.s3_key:
                raise FileNotFoundError(f"no active version of {source} (needed by {', '.join(targets)})")
            _materialize(store, version.s3_key, work_dir)
        with gv.use_output_dir(work_dir):
            gv.export_data_to_db(only=targets, strict=True)   # commits the is_active flip with the facts
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _rebuild_das(spec, run, store):
    from data_viz.das_ingest import ingest_das_file
    work_dir = tempfile.mkdtemp(prefix="rebuild-das-")
    try:
        path = _materialize(store, run.s3_key, work_dir)
        ingest_das_file(path, run.scraped_on, run.data_until, commit=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


REBUILDERS = {"v1": _rebuild_v1, "das": _rebuild_das}


_PUBLISHABLE_STATUSES = (ScrapeRun.STATUS_HELD, ScrapeRun.STATUS_RUNNING, ScrapeRun.STATUS_VALIDATING)


def _reconcile_after_commit(spec):
    # reconcile_source_aliases() runs AFTER the caller's flip has committed (is_active changed, the
    # rebuild's/replay's facts are in), and only for V1 sources (DAS has no source-alias concept). A
    # failure here never touches the run's status -- the publish/rollback already succeeded and is
    # live. Shared by publish_run and rollback_source so neither duplicates this guard.
    if spec.kind != "v1":
        return
    try:
        from data_viz.auth.auth_helpers import reconcile_source_aliases
        reconcile_source_aliases()
    except Exception:
        logger.exception("reconcile_source_aliases failed after publishing %s", spec.key)
        db.session.rollback()


def publish_run(run_id, user_id=None, *, storage=None):
    run = db.session.get(ScrapeRun, run_id)
    if run.status not in _PUBLISHABLE_STATUSES:
        # held = manual publish of a previously-held run; running/validating = the internal
        # _decide() auto-publish path (mid scrape/upload, before its own status transition).
        raise ValueError(f"run {run_id} is {run.status}, not publishable")
    spec = registry.get_source(run.source_key)
    store = storage or storage_mod.get_storage()
    previous = active_run(run.source_key)
    try:
        if previous is not None and previous.id != run.id:
            previous.is_active = False
            db.session.flush()   # partial unique index: clear the old active before setting the new
        run.is_active = True
        run.status = ScrapeRun.STATUS_PUBLISHED
        run.finished_at = _now()
        if user_id is not None:
            run.decided_by_user_id, run.decided_at = user_id, _now()
            log_activity(user_id, "scrape_published", run.id, f"Published {run.source_key} (data until {run.data_until})")
        REBUILDERS[spec.kind](spec, run, store)
        settings_for(run.source_key).consecutive_failures = 0
        db.session.commit()
    except Exception:
        db.session.rollback()
        run = db.session.get(ScrapeRun, run_id)
        run.status = ScrapeRun.STATUS_PUBLISH_FAILED
        run.error = traceback.format_exc()
        run.finished_at = _now()
        if user_id is not None:
            log_activity(user_id, "scrape_publish_failed", run.id,
                        f"Publish failed for {run.source_key} run {run.id}: {run.error}")
        db.session.commit()
        return run

    _reconcile_after_commit(spec)
    return run


def rollback_candidates(key):
    """Published runs of `key` a site admin may roll back to (newest first): anything that passed its
    checks and went live, except the currently active one and the rollback log rows themselves."""
    return (ScrapeRun.query
            .filter(ScrapeRun.source_key == key, ScrapeRun.status == ScrapeRun.STATUS_PUBLISHED,
                    ScrapeRun.trigger != ScrapeRun.TRIGGER_ROLLBACK, ScrapeRun.is_active.is_(False))
            .order_by(ScrapeRun.data_until.desc(), ScrapeRun.id.desc()).all())


def _replay_das(target, store):
    """Rebuild das_* from scratch: every published DAS version up to and including `target`, oldest
    first, in ONE transaction (the Explorer never sees empty tables)."""
    from data_viz import das_ingest
    chain = (ScrapeRun.query
             .filter(ScrapeRun.source_key == target.source_key, ScrapeRun.status == ScrapeRun.STATUS_PUBLISHED,
                     ScrapeRun.trigger != ScrapeRun.TRIGGER_ROLLBACK, ScrapeRun.data_until <= target.data_until)
             .order_by(ScrapeRun.data_until, ScrapeRun.id).all())
    work_dir = tempfile.mkdtemp(prefix="replay-das-")
    try:
        das_ingest.clear_das_tables()
        for version in chain:
            path = _materialize(store, version.s3_key, work_dir)   # names are unique by their dates
            das_ingest.ingest_das_file(path, version.scraped_on, version.data_until, commit=False)
            os.remove(path)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def rollback_source(key, target_run_id, user_id, *, storage=None):
    spec = registry.get_source(key)
    store = storage or storage_mod.get_storage()
    target = db.session.get(ScrapeRun, target_run_id)
    if target is None or target.source_key != key or target.status != ScrapeRun.STATUS_PUBLISHED \
            or target.trigger == ScrapeRun.TRIGGER_ROLLBACK:
        raise ValueError("rollback target must be an earlier published run of this source")
    # settings_for() commits if it has to create the SourceSettings row -- fetch it BEFORE the log row
    # exists / is_active is touched, so that implicit commit can't prematurely persist the flips below
    # (which would make a later rebuild/replay failure unrecoverable).
    settings = settings_for(key)
    previous = active_run(key)
    log_row = ScrapeRun(source_key=key, trigger=ScrapeRun.TRIGGER_ROLLBACK, status=ScrapeRun.STATUS_RUNNING,
                        triggered_by_user_id=user_id, rollback_of_run_id=target.id, s3_key=target.s3_key,
                        data_until=target.data_until, scraped_on=target.scraped_on,
                        previous_data_until=previous.data_until if previous else None,
                        data_source_id=target.data_source_id)
    db.session.add(log_row)
    db.session.commit()
    try:
        if previous is not None and previous.id != target.id:
            previous.is_active = False
            db.session.flush()   # partial unique index: clear the old active before setting the new
        target.is_active = True
        settings.auto_publish = False   # pin: a re-scrape of the same bad data must not auto-publish again
        settings.updated_by_user_id, settings.updated_at = user_id, _now()
        log_activity(user_id, "scrape_rollback", log_row.id,
                     f"Rolled {key} back to run {target.id} (data until {target.data_until})")
        if spec.kind == "das":
            _replay_das(target, store)
        else:
            REBUILDERS["v1"](spec, target, store)
        log_row.status = ScrapeRun.STATUS_PUBLISHED
        log_row.finished_at = _now()
        db.session.commit()
    except Exception:
        db.session.rollback()
        log_row = db.session.get(ScrapeRun, log_row.id)
        log_row.status = ScrapeRun.STATUS_PUBLISH_FAILED
        log_row.error = traceback.format_exc()
        log_row.finished_at = _now()
        if user_id is not None:
            log_activity(user_id, "scrape_rollback_failed", log_row.id,
                        f"Rollback failed for {key} to run {target.id}: {log_row.error}")
        db.session.commit()
        return log_row

    _reconcile_after_commit(spec)
    return log_row


def discard_run(run_id, user_id):
    run = db.session.get(ScrapeRun, run_id)
    if run.status != ScrapeRun.STATUS_HELD:
        raise ValueError(f"run {run_id} is {run.status}, not held")
    run.status = ScrapeRun.STATUS_DISCARDED
    run.decided_by_user_id, run.decided_at = user_id, _now()
    log_activity(user_id, "scrape_discarded", run.id, f"Discarded {run.source_key} run {run.id}")
    db.session.commit()
    return run


_BOOTSTRAP_NAME = re.compile(r"(\d{8})_(\d{8})_([A-Za-z]+)\.(csv|xlsx)")


def bootstrap_sources(output_dir, *, storage=None):
    """One-time: archive the files already in output/ as published runs (the site is already built
    from them, so nothing is rebuilt) and mark the newest per source active. Idempotent by content hash."""
    store = storage or storage_mod.get_storage()
    stats = {"uploaded": 0, "skipped": 0}
    found = []
    for name in sorted(os.listdir(output_dir)):
        match = _BOOTSTRAP_NAME.fullmatch(name)
        if not match or match.group(3) not in registry.SOURCES:
            continue
        scraped = datetime.datetime.strptime(match.group(1), "%Y%m%d").date()
        until = datetime.datetime.strptime(match.group(2), "%Y%m%d").date()
        found.append((match.group(3), until, scraped, os.path.join(output_dir, name)))
    for key, until, scraped, path in sorted(found):
        digest = checks.file_hash(path)
        if ScrapeRun.query.filter_by(source_key=key, content_hash=digest).first():
            stats["skipped"] += 1
            continue
        spec = registry.get_source(key)
        run = ScrapeRun(source_key=key, trigger=ScrapeRun.TRIGGER_BOOTSTRAP, status=ScrapeRun.STATUS_PUBLISHED,
                        data_until=until, scraped_on=scraped, content_hash=digest, finished_at=_now(),
                        data_source_id=_data_source_id(spec))
        db.session.add(run)
        db.session.flush()
        run.s3_key = _archive(store, run, path, "scrapes")
        stats["uploaded"] += 1
    db.session.flush()
    for key in {k for k, *_ in found}:
        newest = (ScrapeRun.query.filter_by(source_key=key, status=ScrapeRun.STATUS_PUBLISHED)
                  .order_by(ScrapeRun.data_until.desc(), ScrapeRun.id.desc()).first())
        if newest and not newest.is_active and active_run(key) is None:
            newest.is_active = True
    db.session.commit()
    return stats


def sweep_stuck_runs(now=None):
    """Only status == running is swept. A "validating" upload is just queued -- process_upload
    flips it to running (and commits) the moment a worker actually picks it up, so a worker dying
    before that point leaves it validating (correctly left alone) rather than orphaned as running."""
    now = now or _now()
    swept = 0
    for run in ScrapeRun.query.filter(ScrapeRun.status == ScrapeRun.STATUS_RUNNING).all():
        spec = registry.SOURCES.get(run.source_key)
        limit = datetime.timedelta(seconds=(spec.time_limit if spec else 900)) + SWEEP_MARGIN
        if now - run.started_at > limit:
            counts = run.trigger in (ScrapeRun.TRIGGER_SCHEDULE, ScrapeRun.TRIGGER_MANUAL)
            _record_failure(run, "worker lost (run exceeded its time limit without finishing)",
                            settings_for(run.source_key), counts=counts)
            swept += 1
    return swept
