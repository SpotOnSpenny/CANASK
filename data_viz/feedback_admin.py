# Site-admin "Feedback" page: the review UI for feedback_submissions (the system of record for the
# public feedback form -- see the /feedback route in data_viz/main.py). Lists submissions as
# collapsible cards; mark addressed / reopen / delete / timestamped notes. Every mutation appends a
# UserActivity row. Site-admin-only, enforced inline (require_role can't express it: site admins
# bypass it), following the pattern of the make-admin / remove-admin routes in data_viz/auth/auth.py.

from flask import Blueprint, abort, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user

from data_viz.auth.auth import require_auth
from data_viz.database import db
from data_viz.database.models import FeedbackNote, FeedbackSubmission, UserActivity
from data_viz.validation import validate_text, MAX_FEEDBACK_NOTE

feedback_admin_blueprint = Blueprint("feedback_admin", __name__)

STATUSES = ("open", "addressed", "all")
CARD_TEMPLATE = "v1/partials/feedback_card.jinja"


def _status():
    """The list filter from the query string; anything unknown is the default (open)."""
    status = request.args.get("status", "open")
    return status if status in STATUSES else "open"


def _items(status):
    query = FeedbackSubmission.query
    if status == "open":
        query = query.filter(FeedbackSubmission.addressed_at.is_(None))
    elif status == "addressed":
        query = query.filter(FeedbackSubmission.addressed_at.isnot(None))
    return query.order_by(FeedbackSubmission.created_at.desc(), FeedbackSubmission.id.desc()).all()


def _require_site_admin():
    if not current_user.site_admin:
        abort(403)


def _submission_or_none(feedback_id):
    # Callers answer None with a direct ("", 404): the app's global errorhandler(404) redirects
    # abort(404) to /not-found (a 302) for mistyped-URL navigation, which would break these
    # API-style HTMX responses.
    return db.session.get(FeedbackSubmission, feedback_id)


def _card(submission):
    # Mutation responses re-render the card expanded (outerHTML swap on #feedback-<id>) so the
    # admin sees the result of what they just did.
    return render_template(CARD_TEMPLATE, s=submission, expanded=True)


def _log(activity_type, target_id, details):
    db.session.add(UserActivity(
        user_id = current_user.id,
        activity_type = activity_type,
        activity_target_type = "feedback",
        activity_target_id = target_id,
        details = details[:5000],
        ip_address = request.remote_addr))
    db.session.commit()


@feedback_admin_blueprint.route("/v1/admin/feedback", methods=["GET"])
@require_auth
def page():
    if not current_user.site_admin:
        flash("That page isn't available.", "danger")
        if request.headers.get("HX-Request") == "true":
            return ("", 204, {"HX-Redirect": url_for("main.index")})
        return redirect(url_for("main.index"))
    status = _status()
    context = {"items": _items(status), "status": status}
    if request.headers.get("HX-Request") == "true":
        return render_template("v1/feedback_admin.jinja", **context)
    return render_template("base.jinja", include_partials="index",
                           dash_template="v1/feedback_admin.jinja", **context)


@feedback_admin_blueprint.route("/v1/admin/feedback/<int:feedback_id>/address", methods=["POST"])
@require_auth
def address(feedback_id):
    _require_site_admin()
    submission = _submission_or_none(feedback_id)
    if submission is None:
        return "", 404
    submission.addressed_at = db.func.current_timestamp()
    submission.addressed_by = current_user.id
    _log("feedback_addressed", submission.id,
         f"Feedback #{submission.id} marked addressed by {current_user.username}.")
    flash(f"Feedback #{submission.id} marked as addressed.", "success")
    return _card(submission)


@feedback_admin_blueprint.route("/v1/admin/feedback/<int:feedback_id>/reopen", methods=["POST"])
@require_auth
def reopen(feedback_id):
    _require_site_admin()
    submission = _submission_or_none(feedback_id)
    if submission is None:
        return "", 404
    submission.addressed_at = None
    submission.addressed_by = None
    _log("feedback_reopened", submission.id,
         f"Feedback #{submission.id} reopened by {current_user.username}.")
    flash(f"Feedback #{submission.id} reopened.", "success")
    return _card(submission)


@feedback_admin_blueprint.route("/v1/admin/feedback/<int:feedback_id>/delete", methods=["POST"])
@require_auth
def delete(feedback_id):
    _require_site_admin()
    submission = _submission_or_none(feedback_id)
    if submission is None:
        return "", 404
    # The activity row is the only record left after a hard delete, so it carries an excerpt.
    details = (f"Feedback #{submission.id} from {submission.name or 'Anonymous'} "
               f"({submission.email or 'no email'}) deleted by {current_user.username}: "
               f"{submission.body[:500]}")
    db.session.delete(submission)   # notes go with it (cascade)
    _log("feedback_deleted", feedback_id, details)
    flash(f"Feedback #{feedback_id} deleted.", "success")
    # Empty body + outerHTML swap removes the card; the after_request hook still appends the flash
    # OOB because this is a 200 text/html response.
    return make_response("", 200)


@feedback_admin_blueprint.route("/v1/admin/feedback/<int:feedback_id>/notes", methods=["POST"])
@require_auth
def add_note(feedback_id):
    _require_site_admin()
    submission = _submission_or_none(feedback_id)
    if submission is None:
        return "", 404
    ok, text = validate_text(request.form.get("text"), "Note", MAX_FEEDBACK_NOTE,
                             required=True, multiline=True)
    if not ok:
        flash(text, "danger")
        return _card(submission)
    note = FeedbackNote(feedback_id=submission.id, author_id=current_user.id, text=text)
    db.session.add(note)
    db.session.flush()
    _log("feedback_note_added", submission.id,
         f"Note #{note.id} added to feedback #{submission.id} by {current_user.username}.")
    db.session.refresh(submission)
    return _card(submission)


@feedback_admin_blueprint.route("/v1/admin/feedback/<int:feedback_id>/notes/<int:note_id>/delete",
                                methods=["POST"])
@require_auth
def delete_note(feedback_id, note_id):
    _require_site_admin()
    submission = _submission_or_none(feedback_id)
    note = db.session.get(FeedbackNote, note_id)
    if submission is None or note is None or note.feedback_id != submission.id:
        return "", 404
    db.session.delete(note)
    _log("feedback_note_deleted", submission.id,
         f"Note #{note_id} deleted from feedback #{submission.id} by {current_user.username}.")
    db.session.refresh(submission)
    return _card(submission)
