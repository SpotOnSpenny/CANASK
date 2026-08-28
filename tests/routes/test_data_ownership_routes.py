"""Data-ownership bulk routes: per-source visibility and group visual grants (the
reconcile the modal's select-all controls rely on)."""
from data_viz.database.models import GroupVisuals, Visuals

from tests.factories import (
    grant_data_source,
    grant_visual,
    make_data_source,
    make_group,
    make_user,
    make_visual,
    unique,
)


def _owned_source(role="Data Owner"):
    """A data source + a group granted it + a user with `role` in that group."""
    source = make_data_source()
    group = make_group()
    grant_data_source(group, source)
    user = make_user(group=group, role=role)
    return source, group, user


class TestBulkSetVisibility:
    def test_owner_sets_all_visuals(self, client, db_session, login_as):
        source, _, owner = _owned_source()
        visuals = [make_visual(visibility="public", data_source=source),
                   make_visual(visibility="group", data_source=source)]
        login_as(owner)
        response = client.post(f"/v1/sources/{source.id}/visibility",
                               data={"visibility": "private"})
        assert response.status_code == 200
        assert f'id="vis-section-{source.id}"' in response.get_data(as_text=True)
        assert all(v.visibility == "private" for v in visuals)

    def test_non_owner_is_redirected_without_change(self, client, db_session, login_as):
        source, _, _ = _owned_source()
        visual = make_visual(visibility="public", data_source=source)
        outsider_group = make_group()
        login_as(make_user(group=outsider_group, role="Data Owner"))
        response = client.post(f"/v1/sources/{source.id}/visibility",
                               data={"visibility": "private"})
        assert response.status_code == 302
        assert visual.visibility == "public"

    def test_invalid_level_flashes_and_rerenders_unchanged(self, client, db_session,
                                                           login_as):
        source, _, owner = _owned_source()
        visual = make_visual(visibility="public", data_source=source)
        login_as(owner)
        response = client.post(f"/v1/sources/{source.id}/visibility",
                               data={"visibility": "banana"})
        assert response.status_code == 200
        assert visual.visibility == "public"

    def test_unknown_source_redirects(self, client, db_session, login_as):
        login_as(make_user(site_admin=True))
        response = client.post("/v1/sources/999999/visibility",
                               data={"visibility": "private"})
        assert response.status_code == 302

    def test_htmx_permission_failure_sends_hx_redirect(self, client, db_session, login_as):
        # An HTMX post must not get a 302 (it would swap the full dashboard into the partial
        # target) -- the bounce is an HX-Redirect full-page navigation instead.
        source, _, _ = _owned_source()
        visual = make_visual(visibility="public", data_source=source)
        login_as(make_user(group=make_group(), role="Data Owner"))
        response = client.post(f"/v1/sources/{source.id}/visibility",
                               data={"visibility": "private"},
                               headers={"HX-Request": "true"})
        assert response.status_code == 204
        assert response.headers["HX-Redirect"] == "/v1/data-ownership"
        assert visual.visibility == "public"

    def test_all_bulk_levels_require_confirm(self, client, db_session, login_as):
        # Every Set-all level -- including Public, the riskiest direction -- carries hx-confirm.
        source, _, owner = _owned_source()
        make_visual(visibility="private", data_source=source)
        login_as(owner)
        body = client.post(f"/v1/sources/{source.id}/visibility",
                           data={"visibility": "group"}).get_data(as_text=True)
        buttons = [chunk for chunk in body.split("<button")[1:]
                   if f"/v1/sources/{source.id}/visibility" in chunk]
        assert len(buttons) == 3
        assert all("hx-confirm=" in chunk for chunk in buttons)


class TestGroupSourceVisualsModal:
    def test_modal_renders_select_all_controls(self, client, db_session, login_as):
        source, group, owner = _owned_source()
        province = unique("prov")
        make_visual(province=province, data_source=source)
        login_as(owner)
        body = client.get(f"/v1/groups/{group.id}/sources/{source.id}/visuals"
                          ).get_data(as_text=True)
        assert 'data-scope="all"' in body
        assert f'data-scope="{province}"' in body
        assert f'data-province="{province}"' in body


class TestGroupSourceVisualsBulkGrant:
    def test_full_grant_across_provinces_and_drill_levels(self, client, db_session,
                                                          login_as):
        source, group, owner = _owned_source()
        province = unique("prov")
        parent = make_visual(province=province, name="parent", data_source=source,
                             next_vis_name="child")
        child = make_visual(province=province, name="child", data_source=source,
                            vis_parent_name="parent")
        other = make_visual(province=unique("prov"), data_source=source)
        login_as(owner)
        response = client.post(
            f"/v1/groups/{group.id}/sources/{source.id}/visuals",
            data={"visual_ids": [str(parent.id), str(child.id), str(other.id)]})
        assert response.status_code == 200
        granted = {gv.visual_id for gv in
                   GroupVisuals.query.filter_by(group_id=group.id).all()}
        assert granted == {parent.id, child.id, other.id}

    def test_orphan_child_is_dropped(self, client, db_session, login_as):
        source, group, owner = _owned_source()
        province = unique("prov")
        parent = make_visual(province=province, name="parent", data_source=source,
                             next_vis_name="child")
        child = make_visual(province=province, name="child", data_source=source,
                            vis_parent_name="parent")
        login_as(owner)
        client.post(f"/v1/groups/{group.id}/sources/{source.id}/visuals",
                    data={"visual_ids": [str(child.id)]})
        assert GroupVisuals.query.filter_by(group_id=group.id).count() == 0

    def test_empty_submit_clears_source_grants_only(self, client, db_session, login_as):
        source, group, owner = _owned_source()
        visual = make_visual(data_source=source)
        grant_visual(group, visual)
        other_source = make_data_source()
        grant_data_source(group, other_source)
        other_visual = make_visual(data_source=other_source)
        grant_visual(group, other_visual)
        login_as(owner)
        client.post(f"/v1/groups/{group.id}/sources/{source.id}/visuals", data={})
        granted = {gv.visual_id for gv in
                   GroupVisuals.query.filter_by(group_id=group.id).all()}
        assert granted == {other_visual.id}
