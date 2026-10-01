# Site-admin "Feedback" page: the review UI for feedback_submissions (the system of record for the
# public feedback form -- see the /feedback route in data_viz/main.py). Lists submissions as
# collapsible cards; mark addressed / reopen / delete / timestamped notes. Every mutation appends a
# UserActivity row. Site-admin-only, enforced inline: require_role gates on group-membership roles
# and site-admin is a per-user flag (User.site_admin), not a group role -- the same pattern as the
# make-admin / remove-admin routes in data_viz/auth/auth.py, including their refusal shape.

from flask import (Blueprint, abort, current_app, flash, make_response, redirect, render_template,
                   request, url_for)
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, selectinload

from data_viz.auth.auth import require_auth
from data_viz.database import db
from data_viz.database.models import FeedbackNote, FeedbackSubmission, UserActivity
from data_viz.validation import validate_text, MAX_FEEDBACK_NOTE

feedback_admin_blueprint = Blueprint("feedback_admin", __name__)

STATUSES = ("open", "addressed", "all")
CARD_TEMPLATE = "v1/partials/feedback_card.jinja"
# Newest-first cap on the list. "Open" stays small by construction; "All" grows forever, and the
# page has no pagination yet -- the template says so when the cap is hit.
MAX_LISTED = 500


def _status():
    """The list filter from the query string; anything unknown is the default (open)."""
    status = request.args.get("status", "open")
    return status if status in STATUSES else "open"


def _items(status):
    # Eager-load everything a card renders (notes + their authors for the badge count and the
    # notes list, the submitter, the addresser) so the list is a fixed number of queries.
    query = FeedbackSubmission.query.options(
        selectinload(FeedbackSubmission.notes).joinedload(FeedbackNote.author),
        joinedload(FeedbackSubmission.user),
        joinedload(FeedbackSubmission.addressed_by_user))
    if status == "open":
        query = query.filter(FeedbackSubmission.addressed_at.is_(None))
    elif status == "addressed":
        query = query.filter(FeedbackSubmission.addressed_at.isnot(None))
    return (query.order_by(FeedbackSubmission.created_at.desc(), FeedbackSubmission.id.desc())
            .limit(MAX_LISTED).all())


def _refuse_non_admin():
    """None for site admins; otherwise the refusal response for a mutation route. Under HTMX the
    message rides back as an OOB flash with nothing swapped (htmx won't render a 4xx body, so a
    bare abort would be a silent no-op -- same shape as auth._admin_gate_refused); a direct
    request gets a plain 403. Only site admins ever see these buttons, so this is defence in depth."""
    if current_user.site_admin:
        return None
    if request.headers.get("HX-Request") == "true":
        flash("Only site admins can manage feedback.", "danger")
        return "", 200, {"HX-Reswap": "none"}
    abort(403)


def _submission_or_none(feedback_id):
    # Callers answer None with a direct ("", 404): the app's global errorhandler(404) redirects
    # abort(404) to /not-found (a 302) for mistyped-URL navigation, which would break these
    # API-style HTMX responses. (The client surfaces the 404 via the htmx:responseError hook on
    # the page container -- e.g. another admin already deleted the card.)
    return db.session.get(FeedbackSubmission, feedback_id)


def _card(submission):
    # Mutation responses re-render the card expanded (outerHTML swap on #feedback-<id>) so the
    # admin sees the result of what they just did.
    return render_template(CARD_TEMPLATE, s=submission, expanded=True)


def _log(activity_type, target_id, details):
    """Append the UserActivity row and commit the whole staged mutation with it. Returns False
    when the commit fails: the transaction is rolled back (nothing changed), the failure is logged
    with its context, and a flash explains -- the route then re-renders the card in its unchanged
    state so the admin sees that nothing happened."""
    db.session.add(UserActivity(
        user_id = current_user.id,
        activity_type = activity_type,
        activity_target_type = "feedback",
        activity_target_id = target_id,
        details = details[:5000],
        ip_address = request.remote_addr))
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()   # expires everything: the re-rendered card reads the DB state
        current_app.logger.exception("Feedback #%s: %s failed to commit", target_id, activity_type)
        flash("That change could not be saved. Please try again.", "danger")
        return False
    return True


@feedback_admin_blueprint.route("/v1/admin/feedback", methods=["GET"])
@require_auth
def page():
    if not current_user.site_admin:
        flash("That page isn't available.", "danger")
        if request.headers.get("HX-Request") == "true":
            return ("", 204, {"HX-Redirect": url_for("main.index")})
        return redirect(url_for("main.index"))
    status = _status()
    context = {"items": _items(status), "status": status, "max_listed": MAX_LISTED}
    if request.headers.get("HX-Request") == "true":
        return render_template("v1/feedback_admin.jinja", **context)
    return render_template("base.jinja", include_partials="index",
                           dash_template="v1/feedback_admin.jinja", **context)


@feedback_admin_blueprint.route("/v1/admin/feedback/<int:feedback_id>/address", methods=["POST"])
@require_auth
def address(feedback_id):
    refused = _refuse_non_admin()
    if refused:
        return refused
    submission = _submission_or_none(feedback_id)
    if submission is None:
        return "", 404
    # Idempotent: a double-click or a second admin on a stale view must not overwrite who
    # addressed it first, nor log a duplicate activity row.
    if not submission.is_addressed:
        submission.mark_addressed(current_user)
        if not _log("feedback_addressed", submission.id,
                    f"Feedback #{submission.id} marked addressed by {current_user.username}."):
            return _card(submission)
        flash(f"Feedback #{submission.id} marked as addressed.", "success")
    return _card(submission)


@feedback_admin_blueprint.route("/v1/admin/feedback/<int:feedback_id>/reopen", methods=["POST"])
@require_auth
def reopen(feedback_id):
    refused = _refuse_non_admin()
    if refused:
        return refused
    submission = _submission_or_none(feedback_id)
    if submission is None:
        return "", 404
    if submission.is_addressed:
        submission.reopen()
        if not _log("feedback_reopened", submission.id,
                    f"Feedback #{submission.id} reopened by {current_user.username}."):
            return _card(submission)
        flash(f"Feedback #{submission.id} reopened.", "success")
    return _card(submission)


@feedback_admin_blueprint.route("/v1/admin/feedback/<int:feedback_id>/delete", methods=["POST"])
@require_auth
def delete(feedback_id):
    refused = _refuse_non_admin()
    if refused:
        return refused
    submission = _submission_or_none(feedback_id)
    if submission is None:
        return "", 404
    # The activity row is the only record left after a hard delete, so it carries an excerpt.
    details = (f"Feedback #{submission.id} from {submission.name or 'Anonymous'} "
               f"({submission.email or 'no email'}) deleted by {current_user.username}: "
               f"{submission.body[:500]}")
    db.session.delete(submission)   # notes go with it (DB cascade)
    if not _log("feedback_deleted", feedback_id, details):
        return _card(submission)    # rolled back: the card is still there
    flash(f"Feedback #{feedback_id} deleted.", "success")
    # Empty body + outerHTML swap removes the card; the after_request hook still appends the flash
    # OOB because this is a 200 text/html response.
    return make_response("", 200)


@feedback_admin_blueprint.route("/v1/admin/feedback/<int:feedback_id>/notes", methods=["POST"])
@require_auth
def add_note(feedback_id):
    refused = _refuse_non_admin()
    if refused:
        return refused
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
    if not _log("feedback_note_added", submission.id,
                f"Note #{note.id} added to feedback #{submission.id} by {current_user.username}."):
        return _card(submission)
    db.session.refresh(submission)
    return _card(submission)


@feedback_admin_blueprint.route("/v1/admin/feedback/<int:feedback_id>/notes/<int:note_id>/delete",
                                methods=["POST"])
@require_auth
def delete_note(feedback_id, note_id):
    refused = _refuse_non_admin()
    if refused:
        return refused
    submission = _submission_or_none(feedback_id)
    note = db.session.get(FeedbackNote, note_id)
    if submission is None or note is None or note.feedback_id != submission.id:
        return "", 404
    db.session.delete(note)
    if not _log("feedback_note_deleted", submission.id,
                f"Note #{note_id} deleted from feedback #{submission.id} by {current_user.username}."):
        return _card(submission)
    db.session.refresh(submission)
    return _card(submission)
