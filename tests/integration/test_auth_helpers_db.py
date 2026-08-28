"""auth_helpers mutations: user/group/membership lifecycle + the UserActivity audit
convention (state-changing helpers append an activity row)."""
import pytest

from data_viz.auth.auth_helpers import (
    assign_group,
    can_manage_source,
    create_group,
    create_user,
    get_assignable_roles,
    set_source_visibility,
    set_visual_visibility,
)
from data_viz.database.models import User, UserActivity, UserGroups

from tests.factories import (
    add_membership,
    grant_data_source,
    make_data_source,
    make_group,
    make_user,
    make_visual,
    unique,
)


class TestCreateUser:
    def test_creates_active_user_with_hashed_password(self, db_session):
        name = unique("newuser")
        user = create_user(f"{name}@example.org", name, "Sufficiently-strong-pw1!")
        assert user.id is not None
        assert user.status == User.STATUS_ACTIVE
        assert user.is_active is True
        assert user.password_hash != "Sufficiently-strong-pw1!"
        assert user.password_hash.startswith("$2")  # bcrypt

    def test_logs_creation_activity(self, db_session):
        name = unique("audited")
        user = create_user(f"{name}@example.org", name, "Sufficiently-strong-pw1!")
        activity = UserActivity.query.filter_by(user_id=user.id, activity_type="creation").one()
        assert name in activity.details

    def test_invited_flow_accepts_pending_invite(self, db_session):
        from tests.factories import make_invite
        inviter = make_user(site_admin=True)
        invite = make_invite(sent_by=inviter)
        name = unique("invited")
        create_user(invite.email, name, "Sufficiently-strong-pw1!", invited_by=inviter.id)
        assert invite.status == "accepted"


class TestAssignGroup:
    def test_assigns_role(self, db_session):
        user, group = make_user(), make_group()
        membership = assign_group(user.id, group.id, "Data Viewer")
        assert membership.role == "Data Viewer"
        assert UserGroups.query.filter_by(user_id=user.id, group_id=group.id).count() == 1

    def test_duplicate_assignment_raises(self, db_session):
        user, group = make_user(), make_group()
        assign_group(user.id, group.id, "Data Viewer")
        with pytest.raises(ValueError):
            assign_group(user.id, group.id, "Data Viewer")

    def test_remove_membership(self, db_session):
        user, group = make_user(), make_group()
        assign_group(user.id, group.id, "Data Viewer")
        assign_group(user.id, group.id, "Data Viewer", remove=True)
        assert UserGroups.query.filter_by(user_id=user.id, group_id=group.id).count() == 0

    def test_remove_nonexistent_raises(self, db_session):
        user, group = make_user(), make_group()
        with pytest.raises(ValueError):
            assign_group(user.id, group.id, "Data Viewer", remove=True)


class TestCreateGroup:
    def test_creates_and_audits(self, db_session):
        creator = make_user(site_admin=True)
        group = create_group(unique("team"), creator.id, description="a team")
        assert group.id is not None
        assert UserActivity.query.filter_by(user_id=creator.id).count() >= 1


class TestCanManageSource:
    def test_site_admin_manages_everything(self, db_session):
        admin = make_user(site_admin=True)
        source = make_data_source()
        assert can_manage_source(admin, source.id) is True

    def test_data_owner_of_granted_source(self, db_session):
        source = make_data_source()
        group = make_group()
        grant_data_source(group, source)
        owner = make_user(group=group, role="Data Owner")
        viewer = make_user(group=group, role="Data Viewer")
        assert can_manage_source(owner, source.id) is True
        assert can_manage_source(viewer, source.id) is False

    def test_owner_of_unrelated_group_cannot(self, db_session):
        source = make_data_source()
        other_group = make_group()  # no grant to this source
        owner = make_user(group=other_group, role="Data Owner")
        assert can_manage_source(owner, source.id) is False


class TestSetVisualVisibility:
    def test_sets_and_audits(self, db_session):
        source = make_data_source()
        visual = make_visual(visibility="private", data_source=source)
        result = set_visual_visibility(visual.id, "public")
        assert result == "public"
        assert visual.visibility == "public"

    def test_invalid_visibility_raises(self, db_session):
        visual = make_visual()
        with pytest.raises(ValueError, match="Invalid visibility"):
            set_visual_visibility(visual.id, "banana")

    def test_child_cannot_be_more_open_than_parent(self, db_session):
        province = unique("prov")
        source = make_data_source()
        make_visual(province=province, name="parent", visibility="private",
                    data_source=source, next_vis_name="child")
        child = make_visual(province=province, name="child", visibility="private",
                            data_source=source, vis_parent_name="parent")
        with pytest.raises(ValueError, match="can't be more visible"):
            set_visual_visibility(child.id, "public")

    def test_lowering_parent_clamps_descendants(self, db_session):
        province = unique("prov")
        source = make_data_source()
        parent = make_visual(province=province, name="parent", visibility="public",
                             data_source=source, next_vis_name="child")
        child = make_visual(province=province, name="child", visibility="public",
                            data_source=source, vis_parent_name="parent")
        set_visual_visibility(parent.id, "private")
        assert child.visibility == "private"


class TestSetSourceVisibility:
    def test_sets_all_visuals_of_source(self, db_session):
        province = unique("prov")
        source = make_data_source()
        parent = make_visual(province=province, name="parent", visibility="public",
                             data_source=source, next_vis_name="child")
        child = make_visual(province=province, name="child", visibility="group",
                            data_source=source, vis_parent_name="parent")
        loose = make_visual(visibility="public", data_source=source)
        assert set_source_visibility(source.id, "private") == 3
        assert parent.visibility == child.visibility == loose.visibility == "private"

    def test_other_sources_untouched(self, db_session):
        source, other_source = make_data_source(), make_data_source()
        make_visual(visibility="public", data_source=source)
        other_visual = make_visual(visibility="public", data_source=other_source)
        set_source_visibility(source.id, "private")
        assert other_visual.visibility == "public"

    def test_noop_returns_zero_and_skips_audit(self, db_session):
        actor = make_user(site_admin=True)
        source = make_data_source()
        make_visual(visibility="public", data_source=source)
        assert set_source_visibility(source.id, "public", changed_by=actor.id) == 0
        assert UserActivity.query.filter_by(
            activity_type="source_visibility_updated").count() == 0

    def test_invalid_visibility_raises(self, db_session):
        source = make_data_source()
        with pytest.raises(ValueError, match="Invalid visibility"):
            set_source_visibility(source.id, "banana")

    def test_missing_source_raises(self, db_session):
        with pytest.raises(ValueError, match="Data source not found"):
            set_source_visibility(999999, "public")

    def test_logs_single_summary_activity(self, db_session):
        actor = make_user(site_admin=True)
        source = make_data_source()
        make_visual(visibility="private", data_source=source)
        make_visual(visibility="private", data_source=source)
        make_visual(visibility="group", data_source=source)
        set_source_visibility(source.id, "group", changed_by=actor.id)
        activity = UserActivity.query.filter_by(
            user_id=actor.id, activity_type="source_visibility_updated").one()
        assert activity.activity_target_type == "data_source"
        assert activity.activity_target_id == source.id
        assert "2 of 3 changed" in activity.details

    def test_no_audit_without_actor(self, db_session):
        source = make_data_source()
        make_visual(visibility="private", data_source=source)
        assert set_source_visibility(source.id, "public", changed_by=None) == 1
        assert UserActivity.query.filter_by(
            activity_type="source_visibility_updated").count() == 0

    def test_cross_source_ancestor_blocks_raise(self, db_session):
        # A chain crossing sources: raising this source above the other-source parent's level
        # must refuse rather than leave the child visible-but-unreachable.
        province = unique("prov")
        parent_source, source = make_data_source(), make_data_source()
        make_visual(province=province, name="parent", visibility="group",
                    data_source=parent_source, next_vis_name="child")
        child = make_visual(province=province, name="child", visibility="group",
                            data_source=source, vis_parent_name="parent")
        with pytest.raises(ValueError, match="another data source"):
            set_source_visibility(source.id, "public")
        assert child.visibility == "group"

    def test_cross_source_descendant_blocks_lower(self, db_session):
        # Lowering this source below an other-source drill-down's level must refuse rather than
        # silently clamp a visual in a source the caller may not manage.
        province = unique("prov")
        source, child_source = make_data_source(), make_data_source()
        parent = make_visual(province=province, name="parent", visibility="public",
                             data_source=source, next_vis_name="child")
        make_visual(province=province, name="child", visibility="public",
                    data_source=child_source, vis_parent_name="parent")
        with pytest.raises(ValueError, match="another data source"):
            set_source_visibility(source.id, "private")
        assert parent.visibility == "public"

    def test_same_source_chain_unaffected_by_guard(self, db_session):
        # The uniform level keeps in-source chains consistent, so they need no guard.
        province = unique("prov")
        source = make_data_source()
        parent = make_visual(province=province, name="parent", visibility="private",
                             data_source=source, next_vis_name="child")
        child = make_visual(province=province, name="child", visibility="private",
                            data_source=source, vis_parent_name="parent")
        assert set_source_visibility(source.id, "public") == 2
        assert parent.visibility == child.visibility == "public"


class TestGetAssignableRoles:
    def test_site_admin_gets_all_group_roles(self, db_session):
        admin = make_user(site_admin=True)
        group = make_group()
        assert get_assignable_roles(admin, group.id) == [
            "Data Owner", "Group Admin", "Data Viewer"]

    def test_member_assigns_only_below_own_role(self, db_session):
        group = make_group()
        group_admin = make_user(group=group, role="Group Admin")
        assert get_assignable_roles(group_admin, group.id) == ["Data Viewer"]

    def test_non_member_assigns_nothing(self, db_session):
        outsider = make_user()
        group = make_group()
        assert get_assignable_roles(outsider, group.id) == []
