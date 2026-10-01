"""Site-admin Feedback page (/v1/admin/feedback): lists stored feedback_submissions as collapsible
cards; mark addressed / reopen / delete / timestamped notes, each logged to UserActivity."""
import logging

import pytest

from data_viz.database import db
from data_viz.database.models import FeedbackNote, FeedbackSubmission, UserActivity
from tests.factories import FAILED_SEND_REASON, make_feedback, make_feedback_note, make_group, make_user

PAGE = "/v1/admin/feedback"
HX = {"HX-Request": "true"}
MUTATIONS = ("address", "reopen", "delete", "notes", "note-delete")


@pytest.fixture()
def admin(login_as):
    return login_as(make_user(site_admin=True))


def _activity(user, activity_type, target_id):
    """The activity rows for one (type, target); every row must carry the feedback target type."""
    rows = UserActivity.query.filter_by(user_id=user.id, activity_type=activity_type).all()
    assert all(r.activity_target_type == "feedback" and r.activity_target_id == target_id for r in rows)
    return rows


def _mutation_url(suffix, row, note=None):
    """All five mutation routes, so authorization/404 matrices can't silently skip one."""
    if suffix == "note-delete":
        return f"{PAGE}/{row.id}/notes/{note.id}/delete"
    return f"{PAGE}/{row.id}/{suffix}"


def _flaky_commit(monkeypatch):
    """Make the next db.session.commit() raise. The route's rollback() must land on a SAVEPOINT,
    not on the db_session fixture's outer transaction: commit the fixture rows first (so the
    session's current transaction ends), THEN open a savepoint on every bound connection -- the
    request's session transaction then joins the savepoint (see the rollback note in CLAUDE.md)."""
    from sqlalchemy.exc import OperationalError
    db.session.commit()
    for connection in db.engines.values():
        connection.begin_nested()
    def boom():
        raise OperationalError("commit", {}, Exception("connection dropped"))
    monkeypatch.setattr(db.session, "commit", boom)


class TestAccess:
    def test_anonymous_redirected_to_login(self, client):
        response = client.get(PAGE)
        assert response.status_code == 302
        assert "/v1/login" in response.location

    def test_non_admin_bounced_home_with_flash(self, client, login_as):
        login_as(make_user(group=make_group(), role="Data Owner"))
        response = client.get(PAGE)
        assert response.status_code == 302
        assert response.location.endswith("/")
        assert "That page isn&#39;t available." in client.get("/").get_data(as_text=True)

    def test_non_admin_htmx_gets_hx_redirect(self, client, login_as):
        login_as(make_user())
        response = client.get(PAGE, headers=HX)
        assert response.status_code == 204
        assert response.headers["HX-Redirect"].endswith("/")

    @pytest.mark.parametrize("suffix", MUTATIONS)
    def test_non_admin_mutations_refused(self, client, login_as, suffix):
        actor = login_as(make_user(group=make_group(), role="Data Owner"))
        row = make_feedback(addressed=(suffix == "reopen"), addressed_by=actor if suffix == "reopen" else None)
        note = make_feedback_note(row, actor)
        url = _mutation_url(suffix, row, note)
        # Direct request: a plain 403. Under HTMX: nothing swapped, the refusal rides back as a flash
        # (htmx won't render a 4xx body, so a bare 403 would be a silent no-op).
        assert client.post(url, data={"text": "x"}).status_code == 403
        response = client.post(url, data={"text": "x"}, headers=HX)
        assert response.status_code == 200
        assert response.headers["HX-Reswap"] == "none"
        assert "Only site admins can manage feedback." in response.get_data(as_text=True)
        assert FeedbackSubmission.query.count() == 1 and FeedbackNote.query.count() == 1
        assert FeedbackSubmission.query.one().is_addressed == (suffix == "reopen")
        assert UserActivity.query.filter_by(user_id=actor.id).count() == 0

    @pytest.mark.parametrize("suffix", MUTATIONS)
    def test_anonymous_mutations_redirected_to_login(self, client, suffix):
        author = make_user(site_admin=True)
        row = make_feedback()
        note = make_feedback_note(row, author)
        response = client.post(_mutation_url(suffix, row, note), data={"text": "x"}, headers=HX)
        assert response.status_code == 302
        assert "/v1/login" in response.location
        assert FeedbackSubmission.query.count() == 1 and FeedbackNote.query.count() == 1

    def test_nav_link_only_for_site_admins(self, client, login_as):
        # The menu is part of the full-page shell (base.jinja -> index.jinja -> menu.jinja), not
        # of the HTMX content partial, so load the page without the HX-Request header.
        login_as(make_user(group=make_group(), role="Data Owner"))
        assert PAGE not in client.get("/").get_data(as_text=True)
        login_as(make_user(site_admin=True))
        assert PAGE in client.get("/").get_data(as_text=True)


class TestPage:
    def test_full_page_load_and_htmx_partial(self, client, admin):
        full = client.get(PAGE).get_data(as_text=True)
        assert "<html" in full and 'name="csrf-token"' in full
        partial = client.get(PAGE, headers=HX).get_data(as_text=True)
        assert "<html" not in partial
        assert 'id="feedback-admin-div"' in partial

    def test_empty_state(self, client, admin):
        html = client.get(PAGE, headers=HX).get_data(as_text=True)
        assert "No open feedback" in html

    def test_default_lists_open_only_newest_first(self, client, admin):
        older = make_feedback(body="older open one")
        done = make_feedback(body="already handled", addressed=True, addressed_by=admin)
        newer = make_feedback(body="newer open one")
        html = client.get(PAGE, headers=HX).get_data(as_text=True)
        assert "older open one" in html and "newer open one" in html
        assert "already handled" not in html
        assert html.index("newer open one") < html.index("older open one")
        assert f'id="feedback-{older.id}"' in html and f'id="feedback-{done.id}"' not in html

    def test_status_filters(self, client, admin):
        make_feedback(body="open one")
        make_feedback(body="handled one", addressed=True, addressed_by=admin)
        addressed = client.get(f"{PAGE}?status=addressed", headers=HX).get_data(as_text=True)
        assert "handled one" in addressed and "open one" not in addressed
        everything = client.get(f"{PAGE}?status=all", headers=HX).get_data(as_text=True)
        assert "handled one" in everything and "open one" in everything
        bogus = client.get(f"{PAGE}?status=bogus", headers=HX).get_data(as_text=True)
        assert "open one" in bogus and "handled one" not in bogus

    def test_card_shows_details_and_badges(self, client, admin):
        submitter = make_user(username="reporter")
        row = make_feedback(body="Line one\nLine two", name="Pat", email="pat@example.org",
                            page="/v1/province/ontario?y=2024", email_sent=False, user=submitter)
        make_feedback_note(row, admin, text="first note")
        html = client.get(PAGE, headers=HX).get_data(as_text=True)
        for needle in ("Pat", "pat@example.org", "/v1/province/ontario?y=2024", "Line one",
                       "Email failed", FAILED_SEND_REASON, "reporter", "first note", admin.username):
            assert needle in html, needle
        assert "Email status unknown" not in html

    def test_unrecorded_email_status_is_not_shown_as_failed(self, client, admin):
        # email_sent=False with no reason only happens when the status commit after a send failed
        # (the mail may well have gone out) -- the card must say "unknown", not "failed".
        make_feedback(email_sent=False, email_error=None)
        html = client.get(PAGE, headers=HX).get_data(as_text=True)
        assert "Email status unknown" in html and "status was not recorded" in html
        assert "Email failed" not in html

    def test_list_is_capped_newest_first(self, client, admin, monkeypatch):
        import data_viz.feedback_admin as module
        monkeypatch.setattr(module, "MAX_LISTED", 2)
        rows = [make_feedback(body=f"submission {i}") for i in range(3)]
        html = client.get(PAGE, headers=HX).get_data(as_text=True)
        assert "Showing the newest 2 submissions" in html
        assert "submission 2" in html and "submission 1" in html and "submission 0" not in html
        assert f'id="feedback-{rows[0].id}"' not in html

    def test_stored_html_is_escaped(self, client, admin):
        make_feedback(body="<script>alert(1)</script>", name="<b>x</b>")
        html = client.get(PAGE, headers=HX).get_data(as_text=True)
        assert "<script>" not in html and "&lt;script&gt;" in html


class TestMutations:
    def test_address_marks_and_logs(self, client, admin):
        row = make_feedback()
        response = client.post(f"{PAGE}/{row.id}/address", headers=HX)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert f'id="feedback-{row.id}"' in html and "Addressed" in html
        assert row.addressed_at is not None and row.addressed_by == admin.id
        assert len(_activity(admin, "feedback_addressed", row.id)) == 1

    def test_address_is_idempotent(self, client, admin, login_as):
        # A double-click, or a second admin on a stale view, must not overwrite who addressed it
        # first nor log a duplicate activity row.
        first = admin
        row = make_feedback()
        assert client.post(f"{PAGE}/{row.id}/address", headers=HX).status_code == 200
        stamped = row.addressed_at
        second = login_as(make_user(site_admin=True))
        response = client.post(f"{PAGE}/{row.id}/address", headers=HX)
        assert response.status_code == 200 and f'id="feedback-{row.id}"' in response.get_data(as_text=True)
        assert row.addressed_by == first.id and row.addressed_at == stamped
        assert len(_activity(first, "feedback_addressed", row.id)) == 1
        assert len(_activity(second, "feedback_addressed", row.id)) == 0
        # And reopening an open one is a no-op too.
        open_row = make_feedback()
        assert client.post(f"{PAGE}/{open_row.id}/reopen", headers=HX).status_code == 200
        assert len(_activity(second, "feedback_reopened", open_row.id)) == 0

    def test_reopen_clears_and_logs(self, client, admin):
        row = make_feedback(addressed=True, addressed_by=admin)
        response = client.post(f"{PAGE}/{row.id}/reopen", headers=HX)
        assert response.status_code == 200
        assert row.addressed_at is None and row.addressed_by is None
        assert len(_activity(admin, "feedback_reopened", row.id)) == 1

    def test_half_addressed_state_is_rejected_by_the_db(self, admin):
        from sqlalchemy.exc import IntegrityError
        row = make_feedback()
        row.addressed_at = db.func.current_timestamp()   # ...without addressed_by
        with pytest.raises(IntegrityError, match="ck_feedback_submissions_addressed_pair"):
            db.session.flush()
        db.session.rollback()

    @pytest.mark.parametrize("suffix", ["address", "delete", "notes"])
    def test_commit_failure_rolls_back_flashes_and_rerenders(self, client, admin, monkeypatch, app, caplog, suffix):
        row = make_feedback(body="still here")
        _flaky_commit(monkeypatch)
        with caplog.at_level(logging.ERROR, logger=app.logger.name):
            response = client.post(f"{PAGE}/{row.id}/{suffix}", data={"text": "a note"}, headers=HX)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        # The card comes back in its unchanged state, with the explanation as an OOB flash.
        assert f'id="feedback-{row.id}"' in html and "still here" in html
        assert "That change could not be saved" in html
        assert db.session.get(FeedbackSubmission, row.id) is not None
        assert FeedbackSubmission.query.one().is_addressed is False
        assert FeedbackNote.query.count() == 0
        assert UserActivity.query.filter_by(user_id=admin.id).count() == 0
        assert any(f"Feedback #{row.id}: feedback_" in r.getMessage() and "failed to commit" in r.getMessage()
                   for r in caplog.records)

    def test_delete_removes_row_and_notes(self, client, admin):
        row = make_feedback(body="to be deleted", name="Gone")
        make_feedback_note(row, admin)
        row_id = row.id
        response = client.post(f"{PAGE}/{row_id}/delete", headers=HX)
        assert response.status_code == 200
        assert db.session.get(FeedbackSubmission, row_id) is None
        assert FeedbackNote.query.filter_by(feedback_id=row_id).count() == 0
        (entry,) = _activity(admin, "feedback_deleted", row_id)
        assert "to be deleted" in entry.details
        assert "flashed-messages-container" in response.get_data(as_text=True)

    @pytest.mark.parametrize("suffix", ["address", "reopen", "delete", "notes", "notes/1/delete"])
    def test_unknown_id_404(self, client, admin, suffix):
        assert client.post(f"{PAGE}/999999/{suffix}", data={"text": "x"}, headers=HX).status_code == 404

    def test_unknown_note_id_404(self, client, admin):
        row = make_feedback()
        assert client.post(f"{PAGE}/{row.id}/notes/999999/delete", headers=HX).status_code == 404

    def test_add_note(self, client, admin):
        row = make_feedback()
        response = client.post(f"{PAGE}/{row.id}/notes", data={"text": "Followed up by email"}, headers=HX)
        assert response.status_code == 200
        assert "Followed up by email" in response.get_data(as_text=True)
        (note,) = FeedbackNote.query.filter_by(feedback_id=row.id).all()
        assert note.author_id == admin.id
        assert len(_activity(admin, "feedback_note_added", row.id)) == 1

    @pytest.mark.parametrize("text", ["", "   ", "x" * 2001, "bad\x00char"])
    def test_invalid_note_rejected_with_flash(self, client, admin, text):
        row = make_feedback()
        response = client.post(f"{PAGE}/{row.id}/notes", data={"text": text}, headers=HX)
        assert response.status_code == 200
        assert FeedbackNote.query.count() == 0
        html = response.get_data(as_text=True)
        assert f'id="feedback-{row.id}"' in html and "flashed-messages-container" in html

    def test_delete_note(self, client, admin):
        other_admin = make_user(site_admin=True)
        row = make_feedback()
        note = make_feedback_note(row, other_admin, text="someone else's note")
        response = client.post(f"{PAGE}/{row.id}/notes/{note.id}/delete", headers=HX)
        assert response.status_code == 200
        assert "someone else's note" not in response.get_data(as_text=True)
        assert FeedbackNote.query.count() == 0
        assert len(_activity(admin, "feedback_note_deleted", row.id)) == 1

    def test_delete_note_of_other_submission_404(self, client, admin):
        row, other = make_feedback(), make_feedback()
        note = make_feedback_note(other, admin)
        assert client.post(f"{PAGE}/{row.id}/notes/{note.id}/delete", headers=HX).status_code == 404
        assert FeedbackNote.query.count() == 1
