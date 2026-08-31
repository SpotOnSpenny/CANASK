"""Removal-password + site-admin-removal helpers: shared-secret lifecycle, account
deactivation, and the last-active-admin guard."""
import pytest

from data_viz.auth.auth_helpers import (
    active_site_admins,
    assign_site_admin,
    check_removal_password,
    deactivate_user,
    is_last_active_site_admin,
    removal_password_is_set,
    set_removal_password,
)
from data_viz.database.models import RemovalPassword, User, UserActivity

from tests.factories import make_group, make_user


class TestRemovalPasswordLifecycle:
    def test_unset_by_default(self, db_session):
        assert removal_password_is_set() is False
        assert check_removal_password("anything") is False

    def test_set_and_check_round_trip(self, db_session):
        set_removal_password("Correct-horse-battery-1!")
        assert removal_password_is_set() is True
        assert check_removal_password("Correct-horse-battery-1!") is True
        assert check_removal_password("wrong-password") is False

    def test_empty_candidate_rejected(self, db_session):
        set_removal_password("Correct-horse-battery-1!")
        assert check_removal_password("") is False
        assert check_removal_password(None) is False

    def test_rotation_upserts_single_row(self, db_session):
        set_removal_password("First-password-value-1!")
        set_removal_password("Second-password-value-2!")
        assert RemovalPassword.query.count() == 1
        assert check_removal_password("Second-password-value-2!") is True
        assert check_removal_password("First-password-value-1!") is False

    def test_hash_is_bcrypt_not_plaintext(self, db_session):
        set_removal_password("Correct-horse-battery-1!")
        row = RemovalPassword.query.one()
        assert row.password_hash != "Correct-horse-battery-1!"
        assert row.password_hash.startswith("$2")

    def test_rotation_by_user_logs_rotator(self, db_session):
        admin = make_user(site_admin=True)
        set_removal_password("Correct-horse-battery-1!", changed_by=admin.id)
        activity = UserActivity.query.filter_by(
            activity_type="removal_password_rotated").one()
        assert activity.user_id == admin.id
        assert admin.username in activity.details
        row = RemovalPassword.query.one()
        assert row.updated_by == admin.id

    def test_rotation_by_cli_logs_break_glass(self, db_session):
        set_removal_password("Correct-horse-battery-1!", changed_by=None)
        activity = UserActivity.query.filter_by(
            activity_type="removal_password_rotated").one()
        assert activity.user_id is None
        assert "CLI" in activity.details
        assert RemovalPassword.query.one().updated_by is None

    def test_password_never_logged(self, db_session):
        admin = make_user(site_admin=True)
        set_removal_password("Correct-horse-battery-1!", changed_by=admin.id)
        for activity in UserActivity.query.all():
            assert "Correct-horse-battery-1!" not in (activity.details or "")


class TestDeactivateUser:
    def test_deactivates_and_clears_admin_flag(self, db_session):
        admin = make_user(site_admin=True)
        deactivate_user(admin.id)
        assert admin.status == User.STATUS_DEACTIVATED
        assert admin.is_active is False
        assert admin.site_admin is False

    def test_keeps_group_memberships(self, db_session):
        group = make_group()
        user = make_user(site_admin=True, group=group, role="Data Viewer")
        deactivate_user(user.id)
        from data_viz.database.models import UserGroups
        assert UserGroups.query.filter_by(user_id=user.id).count() == 1

    def test_logs_deactivation(self, db_session):
        actor = make_user(site_admin=True)
        target = make_user(site_admin=True)
        deactivate_user(target.id, deactivated_by=actor.id, ip_address="1.2.3.4")
        activity = UserActivity.query.filter_by(
            activity_type="account_deactivated").one()
        assert activity.user_id == actor.id
        assert activity.activity_target_id == target.id
        assert activity.ip_address == "1.2.3.4"
        assert target.username in activity.details

    def test_missing_user_raises(self, db_session):
        with pytest.raises(ValueError):
            deactivate_user(999999)


class TestLastActiveSiteAdminGuard:
    def test_sole_admin_is_last(self, db_session):
        admin = make_user(site_admin=True)
        assert is_last_active_site_admin(admin.id) is True

    def test_not_last_when_another_active_admin_exists(self, db_session):
        admin = make_user(site_admin=True)
        make_user(site_admin=True)
        assert is_last_active_site_admin(admin.id) is False

    def test_deactivated_admins_do_not_count(self, db_session):
        admin = make_user(site_admin=True)
        other = make_user(site_admin=True, status=User.STATUS_DEACTIVATED)
        assert is_last_active_site_admin(admin.id) is True

    def test_active_site_admins_excludes_inactive_and_non_admins(self, db_session):
        active_admin = make_user(site_admin=True)
        make_user(site_admin=True, status=User.STATUS_DEACTIVATED)
        make_user()  # regular active user
        admins = active_site_admins()
        assert active_admin in admins
        assert all(a.site_admin and a.status == User.STATUS_ACTIVE for a in admins)


class TestRotateRemovalPasswordCli:
    def _run(self, app, *args):
        return app.test_cli_runner().invoke(args=["rotate-removal-password", *args])

    def test_generate_mode_prints_working_secret(self, app, db_session, ses_outbox):
        result = self._run(app)
        assert result.exit_code == 0
        printed = [line for line in result.output.splitlines()
                   if line.startswith("New removal password:")]
        assert len(printed) == 1
        secret = printed[0].split("New removal password:", 1)[1].strip()
        assert check_removal_password(secret) is True

    def test_password_flag_sets_given_value(self, app, db_session, ses_outbox):
        result = self._run(app, "--password", "Explicit-cli-secret-7!")
        assert result.exit_code == 0
        assert check_removal_password("Explicit-cli-secret-7!") is True
        assert "Explicit-cli-secret-7!" not in result.output  # only echo generated ones

    def test_rotates_existing_password(self, app, db_session, ses_outbox):
        set_removal_password("Old-secret-value-5!")
        self._run(app, "--password", "Explicit-cli-secret-7!")
        assert check_removal_password("Explicit-cli-secret-7!") is True
        assert check_removal_password("Old-secret-value-5!") is False
        assert RemovalPassword.query.count() == 1

    def test_rotation_sends_no_email(self, app, db_session, ses_outbox):
        # Deliberate: rotation is a quiet, server-side act. No notification goes out --
        # the audit row is the record -- and the secret is never emailed anywhere.
        make_user(site_admin=True)
        result = self._run(app)
        assert result.exit_code == 0
        assert len(ses_outbox) == 0

    def test_logs_break_glass_rotation(self, app, db_session, ses_outbox):
        self._run(app)
        activity = UserActivity.query.filter_by(
            activity_type="removal_password_rotated").one()
        assert activity.user_id is None
        assert "CLI" in activity.details

    def test_empty_explicit_password_rejected(self, app, db_session, ses_outbox):
        # An empty stored secret can never validate (check_removal_password rejects empty
        # candidates), so accepting it would brick the removal UI.
        result = self._run(app, "--password", "")
        assert result.exit_code != 0
        assert removal_password_is_set() is False

    def test_weak_explicit_password_rejected(self, app, db_session, ses_outbox):
        result = self._run(app, "--password", "weak")
        assert result.exit_code != 0
        assert removal_password_is_set() is False


class TestAssignSiteAdminDetails:
    def test_removal_details_say_removed(self, db_session):
        actor = make_user(site_admin=True)
        target = make_user(site_admin=True)
        assign_site_admin(target.id, remove=True, assigned_by=actor.id)
        activity = UserActivity.query.filter_by(
            activity_type="site_admin_assignment", user_id=target.id).one()
        assert target.site_admin is False
        assert "removed" in activity.details
        assert "granted" not in activity.details
