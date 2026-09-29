# Data Updates: the scrape pipeline's run log + controls. Site admins see every registered source with
# controls and tracebacks; Data Owners see a read-only log of the sources their groups own; everyone
# else is bounced. Routes enqueue Celery work -- the web process never scrapes or cleans.

# Standard Library Imports
import datetime
import logging
import os
import tempfile

# External Imports
from flask import Blueprint, abort, current_app, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user
from werkzeug.utils import secure_filename

# Internal Imports
from celery_worker.tasks.data_collection import enqueue_publish, enqueue_rollback, enqueue_scrape, enqueue_upload_processing
from data_scraping import orchestrator, registry
from data_scraping.storage import get_storage
from data_viz.auth.auth import require_auth
from data_viz.auth.auth_helpers import can_manage_source
from data_viz.database import db
from data_viz.database.models import DataSources, ScrapeRun

logger = logging.getLogger(__name__)

data_updates_blueprint = Blueprint("data_updates", __name__)

PAGE_SIZE = 50


def visible_source_keys(user):
    if not getattr(user, "is_authenticated", False):
        return []
    if user.site_admin:
        return list(registry.SOURCES)
    names = {spec.data_source_name: key for key, spec in registry.SOURCES.items()}
    rows = DataSources.query.filter(DataSources.name.in_(names)).all()
    return [names[row.name] for row in rows if can_manage_source(user, row.id)]


def data_updates_visible(user):
    return bool(visible_source_keys(user))


def _render(template, **context):
    if request.headers.get("HX-Request"):
        return render_template(template, **context)
    return render_template("base.jinja", include_partials="index", dash_template=template, **context)


def _require_site_admin():
    if not current_user.site_admin:
        abort(403)


def _modal_error_response(spec, error):
    """An upload failure that must show up INSIDE the still-open modal. The vendored htmx 1.9.12
    doesn't swap non-2xx responses by default, so this always answers 200 -- HX-Retarget +
    HX-Reswap redirect the swap into the modal body instead of the form's own hx-target (the run
    log), and the upload modal's hx-on::after-request checks for HX-Retarget before closing itself
    so an error never gets silently swallowed by an auto-close."""
    response = make_response(render_template("v1/partials/data_updates_upload_modal.jinja", spec=spec, error=error), 200)
    response.headers["HX-Retarget"] = "#data-updates-modal-content"
    response.headers["HX-Reswap"] = "innerHTML"
    return response


def handle_upload_too_large(error):
    """Registered in data_viz/__init__.py as the app's RequestEntityTooLarge error handler. Only
    the upload route raises its per-request max_content_length above the app-wide MAX_CONTENT_LENGTH
    (see the before_request hook in __init__.py), so it's the only route this can realistically fire
    for -- anything else falls back to werkzeug's plain 413."""
    if request.endpoint != "data_updates.upload" or not getattr(current_user, "site_admin", False):
        return error
    spec = _spec_or_404(request.view_args.get("key")) if request.view_args else None
    if spec is None or not registry.onboarded(spec):
        return error
    limit_mb = current_app.config["SCRAPE_UPLOAD_MAX_BYTES"] // (1024 * 1024)
    return _modal_error_response(spec, f"That file is larger than the {limit_mb} MB upload limit.")


def _spec_or_404(key):
    # A direct 404 response, not abort(404): the app's global errorhandler(404) redirects aborts to
    # /not-found (a 302) for organic mistyped-URL navigation, which would break a deliberate API-style
    # 404 here (see das_explorer.py's routes for the same convention).
    return registry.SOURCES.get(key)


def _source_row(spec):
    settings = orchestrator.settings_for(spec.key)
    active = orchestrator.active_run(spec.key)
    last = ScrapeRun.query.filter_by(source_key=spec.key).order_by(ScrapeRun.started_at.desc()).first()
    held = ScrapeRun.query.filter_by(source_key=spec.key, status=ScrapeRun.STATUS_HELD).count()
    return {"spec": spec, "settings": settings, "active": active, "last": last, "held": held,
            "onboarded": registry.onboarded(spec), "new_data": bool(last and last.notices)}


def _runs_query(keys):
    query = ScrapeRun.query.filter(ScrapeRun.source_key.in_(keys))
    if request.args.get("source") in keys:
        query = query.filter_by(source_key=request.args["source"])
    if request.args.get("status"):
        query = query.filter_by(status=request.args["status"])
    return query.order_by(ScrapeRun.started_at.desc()).limit(PAGE_SIZE).all()


_STATUSES = [ScrapeRun.STATUS_VALIDATING, ScrapeRun.STATUS_RUNNING, ScrapeRun.STATUS_NO_NEW_DATA,
             ScrapeRun.STATUS_REJECTED, ScrapeRun.STATUS_FAILED, ScrapeRun.STATUS_HELD,
             ScrapeRun.STATUS_PUBLISHED, ScrapeRun.STATUS_DISCARDED, ScrapeRun.STATUS_PUBLISH_FAILED]


@data_updates_blueprint.route("/v1/admin/data-updates", methods=["GET"])
@require_auth
def page():
    keys = visible_source_keys(current_user)
    if not keys:
        flash("You do not have access to Data Updates.", "danger")
        return redirect(url_for("main.index"))
    rows = [_source_row(registry.SOURCES[k]) for k in keys]
    return _render("v1/data_updates.jinja", rows=rows, runs=_runs_query(keys),
                   is_admin=current_user.site_admin, statuses=_STATUSES,
                   rollback_ids=_rollback_ids(keys, current_user.site_admin))


@data_updates_blueprint.route("/v1/admin/data-updates/runs", methods=["GET"])
@require_auth
def runs():
    keys = visible_source_keys(current_user)
    if not keys:
        abort(403)
    return render_template("v1/partials/data_updates_runs.jinja", runs=_runs_query(keys),
                           is_admin=current_user.site_admin,
                           rollback_ids=_rollback_ids(keys, current_user.site_admin))


@data_updates_blueprint.route("/v1/admin/data-updates/runs/<int:run_id>", methods=["GET"])
@require_auth
def run_row(run_id):
    run = db.session.get(ScrapeRun, run_id)
    if run is None:
        return "", 404
    if run.source_key not in visible_source_keys(current_user):
        abort(403)
    return render_template("v1/partials/data_updates_run_row.jinja", run=run, is_admin=current_user.site_admin,
                           rollback_ids=_rollback_ids([run.source_key], current_user.site_admin))


def _candidate_ids(key):
    return {r.id for r in orchestrator.rollback_candidates(key)}


def _rollback_ids(keys, is_admin):
    # Only site admins get rollback buttons, so skip the queries for read-only viewers.
    return set().union(*(_candidate_ids(k) for k in keys)) if is_admin else set()


@data_updates_blueprint.route("/v1/admin/data-updates/sources/<key>/settings", methods=["POST"])
@require_auth
def update_setting(key):
    _require_site_admin()
    spec = _spec_or_404(key)
    if spec is None:
        return "", 404
    field = request.form.get("field")
    if field not in ("schedule_enabled", "auto_publish"):
        abort(400)
    orchestrator.set_setting(key, field, request.form.get("value") == "1", current_user.id)
    return render_template("v1/partials/data_updates_source_row.jinja", row=_source_row(spec), is_admin=True)


@data_updates_blueprint.route("/v1/admin/data-updates/subscription", methods=["POST"])
@require_auth
def subscription():
    _require_site_admin()
    current_user.data_report_subscribed = request.form.get("subscribed") == "1"
    orchestrator.log_activity(current_user.id, "data_report_subscription", None,
                              f"Nightly report {'on' if current_user.data_report_subscribed else 'off'}")
    db.session.commit()
    flash("Nightly report " + ("enabled." if current_user.data_report_subscribed else "disabled."), "success")
    # 200, not 204: the after_request hook skips injecting the OOB flash swap for 204/205 responses
    # (their body is never rendered), which would silently drop this flash until the next navigation.
    # The toggle itself uses hx-swap="none", so an empty 200 body swaps nothing either way.
    return make_response("", 200)


def _run_or_404(run_id):
    # Same convention as _spec_or_404 above: return the row or None so the caller can return a bare
    # ("", 404) itself rather than going through abort(404) (which the app's global errorhandler
    # redirects to /not-found -- fine for organic navigation, wrong for a deliberate API-style 404).
    return db.session.get(ScrapeRun, run_id)


@data_updates_blueprint.route("/v1/admin/data-updates/sources/<key>/run", methods=["POST"])
@require_auth
def run_now(key):
    _require_site_admin()
    spec = _spec_or_404(key)
    if spec is None:
        return "", 404
    if not (spec.scrape and registry.onboarded(spec)):
        abort(400)
    enqueue_scrape(key, ScrapeRun.TRIGGER_MANUAL, current_user.id)
    flash(f"{spec.label}: scrape queued.", "success")
    return make_response("", 200)


@data_updates_blueprint.route("/v1/admin/data-updates/sources/<key>/upload", methods=["GET", "POST"])
@require_auth
def upload(key):
    _require_site_admin()
    spec = _spec_or_404(key)
    if spec is None:
        return "", 404
    if not registry.onboarded(spec):
        abort(400)
    if request.method == "GET":
        return render_template("v1/partials/data_updates_upload_modal.jinja", spec=spec, error=None)

    # The actual per-request cap (SCRAPE_UPLOAD_MAX_BYTES) is raised ahead of CSRF's own
    # before_request in __init__.py's _raise_upload_content_length hook -- CSRFProtect parses
    # request.form itself before this view ever runs, so setting it here would be too late. Past
    # that cap, werkzeug raises RequestEntityTooLarge, handled by handle_upload_too_large below.
    file = request.files.get("file")
    ext = os.path.splitext(file.filename or "")[1].lstrip(".").lower() if file else ""
    try:
        data_until = datetime.date.fromisoformat(request.form.get("data_until", ""))
    except ValueError:
        data_until = None
    error = None
    if not file or not file.filename:
        error = "Choose a file to upload."
    elif ext != spec.contract.ext:
        error = f"{spec.label} expects a .{spec.contract.ext} file."
    elif data_until is None:
        error = "Enter the date the data runs until."
    if error:
        return _modal_error_response(spec, error)

    try:
        with tempfile.TemporaryDirectory(prefix="upload-") as work_dir:
            path = os.path.join(work_dir, secure_filename(file.filename) or f"upload.{ext}")
            file.save(path)
            run = orchestrator.create_upload_run(key, path, file.filename, data_until, current_user.id,
                                                 storage=get_storage())
    except Exception:
        # Drop the flushed-but-uncommitted run (or a broken transaction, if the failure was a DB
        # error) before rendering: the error modal's context processors query the session.
        db.session.rollback()
        logger.exception("upload of %s for %s failed while archiving to incoming/", file.filename, key)
        return _modal_error_response(spec, "Upload failed -- please try again.")
    enqueue_upload_processing(run.id, key)
    flash(f"{spec.label}: upload received -- validating.", "success")
    return render_template("v1/partials/data_updates_run_row.jinja", run=run, is_admin=True, rollback_ids=set())


_CONFIRM_COPY = {
    "publish": "Publish this version. The visuals below will be rebuilt from it.",
    "discard": "Discard this held version. It stays in the archive but will never be published.",
    "rollback": "Make this version live again and rebuild the visuals below. Auto-publish will be turned off for this source until you turn it back on.",
}


@data_updates_blueprint.route("/v1/admin/data-updates/runs/<int:run_id>/confirm/<action>", methods=["GET"])
@require_auth
def confirm(run_id, action):
    _require_site_admin()
    if action not in _CONFIRM_COPY:
        return "", 404
    run = _run_or_404(run_id)
    if run is None:
        return "", 404
    spec = _spec_or_404(run.source_key)
    if spec is None:
        return "", 404
    targets = ["DAS Explorer (full replay up to this workbook)"] if spec.kind == "das" else registry.rebuild_targets(spec)
    return render_template("v1/partials/data_updates_confirm_modal.jinja", run=run, spec=spec, action=action,
                           copy=_CONFIRM_COPY[action], targets=targets)


@data_updates_blueprint.route("/v1/admin/data-updates/runs/<int:run_id>/publish", methods=["POST"])
@require_auth
def publish(run_id):
    _require_site_admin()
    run = _run_or_404(run_id)
    if run is None:
        return "", 404
    if run.status != ScrapeRun.STATUS_HELD:
        abort(409)
    enqueue_publish(run.id, current_user.id, run.source_key)
    flash("Publishing -- the run will update when the rebuild finishes.", "success")
    return make_response("", 200)


@data_updates_blueprint.route("/v1/admin/data-updates/runs/<int:run_id>/discard", methods=["POST"])
@require_auth
def discard(run_id):
    _require_site_admin()
    run = _run_or_404(run_id)
    if run is None:
        return "", 404
    if run.status != ScrapeRun.STATUS_HELD:
        abort(409)
    run = orchestrator.discard_run(run.id, current_user.id)
    return render_template("v1/partials/data_updates_run_row.jinja", run=run, is_admin=True, rollback_ids=set())


@data_updates_blueprint.route("/v1/admin/data-updates/runs/<int:run_id>/rollback", methods=["POST"])
@require_auth
def rollback(run_id):
    _require_site_admin()
    run = _run_or_404(run_id)
    if run is None:
        return "", 404
    if run.id not in _candidate_ids(run.source_key):
        abort(409)
    enqueue_rollback(run.source_key, run.id, current_user.id)
    flash("Rollback queued -- the log will show it when the rebuild finishes.", "success")
    return make_response("", 200)
