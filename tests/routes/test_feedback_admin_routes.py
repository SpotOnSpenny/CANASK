"""Site-admin Feedback page (/v1/admin/feedback): lists stored feedback_submissions as collapsible
cards; mark addressed / reopen / delete / timestamped notes, each logged to UserActivity."""
import pytest

from data_viz.database import db
from data_viz.database.models import FeedbackNote, FeedbackSubmission, UserActivity
from tests.factories import make_feedback, make_feedback_note, make_group, make_user

PAGE = "/v1/admin/feedback"
HX = {"HX-Request": "true"}


@pytest.fixture()
def admin(login_as):
    return login_as(make_user(site_admin=True))


def _activity(user, activity_type):
    return UserActivity.query.filter_by(user_id=user.id, activity_type=activity_type).all()


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

    def test_non_admin_htmx_gets_hx_redirect(self, client, login_as):
        login_as(make_user())
        response = client.get(PAGE, headers=HX)
        assert response.status_code == 204
        assert response.headers["HX-Redirect"].endswith("/")

    @pytest.mark.parametrize("suffix", ["address", "reopen", "delete", "notes"])
    def test_non_admin_mutations_403(self, client, login_as, suffix):
        login_as(make_user())
        row = make_feedback()
        assert client.post(f"{PAGE}/{row.id}/{suffix}", data={"text": "x"}, headers=HX).status_code == 403
        assert FeedbackSubmission.query.count() == 1

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
                       "Email failed", "reporter", "first note", admin.username):
            assert needle in html, needle

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
        assert len(_activity(admin, "feedback_addressed")) == 1

    def test_reopen_clears_and_logs(self, client, admin):
        row = make_feedback(addressed=True, addressed_by=admin)
        response = client.post(f"{PAGE}/{row.id}/reopen", headers=HX)
        assert response.status_code == 200
        assert row.addressed_at is None and row.addressed_by is None
        assert len(_activity(admin, "feedback_reopened")) == 1

    def test_delete_removes_row_and_notes(self, client, admin):
        row = make_feedback(body="to be deleted", name="Gone")
        make_feedback_note(row, admin)
        row_id = row.id
        response = client.post(f"{PAGE}/{row_id}/delete", headers=HX)
        assert response.status_code == 200
        assert db.session.get(FeedbackSubmission, row_id) is None
        assert FeedbackNote.query.filter_by(feedback_id=row_id).count() == 0
        (entry,) = _activity(admin, "feedback_deleted")
        assert entry.activity_target_id == row_id and "to be deleted" in entry.details
        assert "flashed-messages-container" in response.get_data(as_text=True)

    @pytest.mark.parametrize("suffix", ["address", "reopen", "delete", "notes"])
    def test_unknown_id_404(self, client, admin, suffix):
        assert client.post(f"{PAGE}/999999/{suffix}", data={"text": "x"}, headers=HX).status_code == 404

    def test_add_note(self, client, admin):
        row = make_feedback()
        response = client.post(f"{PAGE}/{row.id}/notes", data={"text": "Followed up by email"}, headers=HX)
        assert response.status_code == 200
        assert "Followed up by email" in response.get_data(as_text=True)
        (note,) = FeedbackNote.query.filter_by(feedback_id=row.id).all()
        assert note.author_id == admin.id
        assert len(_activity(admin, "feedback_note_added")) == 1

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
        assert len(_activity(admin, "feedback_note_deleted")) == 1

    def test_delete_note_of_other_submission_404(self, client, admin):
        row, other = make_feedback(), make_feedback()
        note = make_feedback_note(other, admin)
        assert client.post(f"{PAGE}/{row.id}/notes/{note.id}/delete", headers=HX).status_code == 404
        assert FeedbackNote.query.count() == 1
