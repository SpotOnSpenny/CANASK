"""Who was invited, when, how and by whom must stay readable after the person changes their own
username or email: an accepted invite is tied to the account it created, the invite list shows
who that account is NOW, and the user list shows how each account came to exist."""
from data_viz.database.models import Invites, User

from tests.factories import (
    TEST_PASSWORD,
    add_membership,
    make_group,
    make_invite,
    make_joined_user,
    make_user,
    unique,
)

HX = {"HX-Request": "true"}


def _row(body, row_id):
    """The HTML of one table row, by its id."""
    start = body.index(f'id="{row_id}"')
    return body[start:body.index("</tr>", start)]


class TestAcceptingLinksTheInvite:
    def test_invite_remembers_the_account_it_created(self, client, db_session, app, celery_stub):
        group = make_group()
        invite = make_invite(groups=[(group, "Data Viewer")])
        invite.generate_jwt(app.config["INVITE_JWT_SECRET"])
        client.get(f"/v1/accept-invite/{invite.token}")
        username = unique("newbie")
        client.post("/v1/accept-invite", data={"username": username, "password": TEST_PASSWORD,
                                              "confirm_password": TEST_PASSWORD})
        db_session.expire_all()
        user = User.query.filter_by(username=username).one()
        assert invite.accepted_user_id == user.id
        assert invite.accepted_username == username
        assert invite.accepted_at is not None

    def test_a_pending_invite_is_tied_to_nobody(self, db_session):
        invite = make_invite()
        assert invite.accepted_user_id is None and invite.accepted_at is None


class TestInviteListFollowsTheAccount:
    def _page(self, client):
        return client.get("/v1/invite-management", headers=HX).get_data(as_text=True)

    def test_accepted_invite_names_the_account(self, client, db_session, login_as):
        user, invite = make_joined_user()
        login_as(make_user(site_admin=True))
        row = _row(self._page(client), f"invite-row-{invite.id}")
        assert invite.email in row
        assert user.username in row

    def test_changed_email_shows_both(self, client, db_session, login_as):
        user, invite = make_joined_user()
        invited_as = invite.email
        user.email = f"{unique('moved')}@example.org"
        db_session.flush()
        login_as(make_user(site_admin=True))
        row = _row(self._page(client), f"invite-row-{invite.id}")
        assert invited_as in row
        assert user.email in row

    def test_changed_username_shows_both(self, client, db_session, login_as):
        user, invite = make_joined_user(username=unique("original"))
        signed_up_as = user.username
        user.username = unique("renamed")
        db_session.flush()
        login_as(make_user(site_admin=True))
        row = _row(self._page(client), f"invite-row-{invite.id}")
        assert user.username in row
        assert signed_up_as in row

    def test_unchanged_account_is_not_cluttered_with_repeats(self, client, db_session, login_as):
        user, invite = make_joined_user()
        login_as(make_user(site_admin=True))
        row = _row(self._page(client), f"invite-row-{invite.id}")
        assert row.count(invite.email) == 1
        assert "signed up as" not in row

    def test_sender_who_renamed_shows_under_the_new_name(self, client, db_session, login_as):
        inviter = make_user(site_admin=True, username=unique("oldsender"))
        _, invite = make_joined_user(inviter=inviter)
        inviter.username = unique("newsender")
        db_session.flush()
        login_as(make_user(site_admin=True))
        assert inviter.username in _row(self._page(client), f"invite-row-{invite.id}")

    def test_pending_invite_has_no_account_line(self, client, db_session, login_as):
        invite = make_invite()
        login_as(make_user(site_admin=True))
        assert "Account:" not in _row(self._page(client), f"invite-row-{invite.id}")

    def test_group_manager_sees_it_for_their_groups_invite(self, client, db_session, login_as):
        group = make_group()
        user, invite = make_joined_user(groups=[(group, "Data Viewer")])
        user.email = f"{unique('moved')}@example.org"
        db_session.flush()
        login_as(make_user(group=group, role="Group Admin"))
        row = _row(self._page(client), f"invite-row-{invite.id}")
        assert user.username in row and user.email in row


class TestUserListShowsHowTheyJoined:
    def _page(self, client):
        return client.get("/v1/user-management", headers=HX).get_data(as_text=True)

    def test_shows_when_and_by_whom(self, client, db_session, login_as):
        inviter = make_user(site_admin=True, username=unique("inviter"))
        user, invite = make_joined_user(inviter=inviter)
        login_as(make_user(site_admin=True))
        row = _row(self._page(client), f"user-row-{user.id}")
        assert f"Invited {invite.sent_at.strftime('%Y-%m-%d')}" in row
        assert f"by {inviter.username}" in row

    def test_inviter_who_renamed_shows_under_the_new_name(self, client, db_session, login_as):
        inviter = make_user(site_admin=True, username=unique("oldname"))
        user, _ = make_joined_user(inviter=inviter)
        inviter.username = unique("newname")
        db_session.flush()
        login_as(make_user(site_admin=True))
        assert f"by {inviter.username}" in _row(self._page(client), f"user-row-{user.id}")

    def test_changed_email_shows_what_they_were_invited_as(self, client, db_session, login_as):
        user, invite = make_joined_user()
        user.email = f"{unique('moved')}@example.org"
        db_session.flush()
        login_as(make_user(site_admin=True))
        row = _row(self._page(client), f"user-row-{user.id}")
        assert user.email in row
        assert f"as {invite.email}" in row

    def test_changed_username_shows_what_they_signed_up_as(self, client, db_session, login_as):
        user, invite = make_joined_user(username=unique("original"))
        user.username = unique("renamed")
        db_session.flush()
        login_as(make_user(site_admin=True))
        assert f"signed up as {invite.accepted_username}" in _row(self._page(client), f"user-row-{user.id}")

    def test_unchanged_account_has_no_formerly_notes(self, client, db_session, login_as):
        user, _ = make_joined_user()
        login_as(make_user(site_admin=True))
        row = _row(self._page(client), f"user-row-{user.id}")
        assert "signed up as" not in row and " as " not in row.split("Invited")[1].split("</div>")[0]

    def test_site_admin_invite_is_called_one(self, client, db_session, login_as):
        user, _ = make_joined_user(site_admin_invite=True)
        login_as(make_user(site_admin=True))
        assert "site admin invite" in _row(self._page(client), f"user-row-{user.id}")

    def test_group_invite_names_the_groups_it_carried(self, client, db_session, login_as):
        group = make_group(name=unique("Harm Reduction Team"))
        user, _ = make_joined_user(groups=[(group, "Data Viewer")])
        login_as(make_user(site_admin=True))
        joined = _row(self._page(client), f"user-row-{user.id}").split("Invited")[1].split("</div>")[0]
        assert group.name in joined

    def test_account_with_no_invite_says_so(self, client, db_session, login_as):
        user = make_user()
        login_as(make_user(site_admin=True))
        assert "No invite on record" in _row(self._page(client), f"user-row-{user.id}")

    def test_group_manager_sees_it_when_the_invite_was_into_their_group(self, client, db_session,
                                                                       login_as):
        group = make_group()
        user, invite = make_joined_user(groups=[(group, "Data Viewer")])
        login_as(make_user(group=group, role="Group Admin"))
        assert "Invited" in _row(self._page(client), f"user-row-{user.id}")

    def test_group_manager_does_not_see_an_invite_into_someone_elses_group(self, client,
                                                                         db_session, login_as):
        """Who invited a person, and into what, says which other groups they belong to."""
        mine = make_group()
        theirs = make_group(name=unique("Somebody Elses Team"))
        inviter = make_user(site_admin=True, username=unique("otherinviter"))
        user, _ = make_joined_user(inviter=inviter, groups=[(theirs, "Data Viewer")])
        add_membership(user, mine, "Data Viewer")
        login_as(make_user(group=mine, role="Group Admin"))
        row = _row(self._page(client), f"user-row-{user.id}")
        assert "Invited" not in row
        assert inviter.username not in row and theirs.name not in row

    def test_group_manager_sees_only_their_own_groups_from_a_shared_invite(self, client,
                                                                         db_session, login_as):
        mine = make_group(name=unique("Mine"))
        theirs = make_group(name=unique("Theirs"))
        user, _ = make_joined_user(groups=[(mine, "Data Viewer"), (theirs, "Data Viewer")])
        login_as(make_user(group=mine, role="Group Admin"))
        row = _row(self._page(client), f"user-row-{user.id}")
        assert "Invited" in row and theirs.name not in row

    def test_directory_rows_say_nothing_about_invites(self, client, db_session, login_as):
        # An explicit address: the factory's default one contains the word "invitee".
        outsider, _ = make_joined_user(email=f"{unique('person')}@example.org")
        login_as(make_user(group=make_group(), role="Group Admin"))
        row = _row(self._page(client), f"user-row-{outsider.id}")
        assert "Invited" not in row and "invite" not in row.lower().split("</td>")[0]
