"""RBAC visibility matrix + menu building against real rows. Uses structural visuals
(metric=None, always 'has data') for the visibility matrix, and metric visuals with
DataPoints for the has-data rules."""
from data_viz.visual_query import (
    accessible_provinces,
    allowed_visuals,
    build_province_menu,
    displayable_visuals,
)

from tests.factories import (
    grant_data_source,
    grant_visual,
    make_data_source,
    make_datapoint,
    make_group,
    make_user,
    make_visual,
    unique,
)


def names(visuals):
    return [v.name for v in visuals]


class TestVisibilityMatrix:
    def test_anonymous_sees_only_public(self, db_session):
        province = unique("prov")
        make_visual(province=province, name="pub", visibility="public")
        make_visual(province=province, name="grp", visibility="group")
        make_visual(province=province, name="priv", visibility="private")
        assert names(allowed_visuals(None, province)) == ["pub"]

    def test_signed_in_without_grants_sees_only_public(self, db_session):
        province = unique("prov")
        make_visual(province=province, name="pub", visibility="public")
        make_visual(province=province, name="grp", visibility="group")
        user = make_user()
        assert names(allowed_visuals(user, province)) == ["pub"]

    def test_group_grant_reveals_group_visual(self, db_session):
        province = unique("prov")
        visual = make_visual(province=province, name="grp", visibility="group")
        group = make_group()
        grant_visual(group, visual)
        member = make_user(group=group, role="Data Viewer")
        outsider = make_user()
        assert names(allowed_visuals(member, province)) == ["grp"]
        assert names(allowed_visuals(outsider, province)) == []

    def test_data_owner_of_source_sees_private(self, db_session):
        province = unique("prov")
        source = make_data_source()
        make_visual(province=province, name="priv", visibility="private", data_source=source)
        group = make_group()
        grant_data_source(group, source)
        owner = make_user(group=group, role="Data Owner")
        viewer = make_user(group=group, role="Data Viewer")  # below Data Owner: no ownership
        assert names(allowed_visuals(owner, province)) == ["priv"]
        assert names(allowed_visuals(viewer, province)) == []

    def test_site_admin_sees_everything(self, db_session):
        province = unique("prov")
        make_visual(province=province, name="priv", visibility="private")
        admin = make_user(site_admin=True)
        assert names(allowed_visuals(admin, province)) == ["priv"]

    def test_orphaned_drill_child_pruned(self, db_session):
        province = unique("prov")
        make_visual(province=province, name="parent", visibility="private",
                    next_vis_name="child")
        make_visual(province=province, name="child", visibility="public",
                    vis_parent_name="parent")
        # Child is public but its parent is hidden from anonymous -> pruned.
        assert names(allowed_visuals(None, province)) == []


class TestHasData:
    def _metric_visual(self, province, **kw):
        source = make_data_source()
        visual = make_visual(province=province, visibility="public", metric="deaths",
                             geo_type="province", data_source=source, **kw)
        return visual, source

    def test_visual_with_nonzero_fact_displays(self, db_session):
        province = unique("prov")
        visual, source = self._metric_visual(province)
        make_datapoint(source, geo=province, data_metric="deaths", data_value=5.0)
        from data_viz.database.models import VisualQuery
        from data_viz.database import db
        db.session.add(VisualQuery(filter_type="geo", filter_value=province,
                                   for_visual_id=visual.id))
        db.session.flush()
        assert names(displayable_visuals(None, province)) == [visual.name]

    def test_province_visual_without_geo_predicate_hidden(self, db_session):
        province = unique("prov")
        visual, source = self._metric_visual(province)
        make_datapoint(source, geo=province, data_metric="deaths", data_value=5.0)
        # No VisualQuery geo predicate -> not scoped to a geo -> no data of its own.
        assert names(displayable_visuals(None, province)) == []

    def test_all_zero_series_hidden(self, db_session):
        province = unique("prov")
        visual, source = self._metric_visual(province)
        make_datapoint(source, geo=province, data_metric="deaths", data_value=0.0)
        from data_viz.database.models import VisualQuery
        from data_viz.database import db
        db.session.add(VisualQuery(filter_type="geo", filter_value=province,
                                   for_visual_id=visual.id))
        db.session.flush()
        assert names(displayable_visuals(None, province)) == []

    def test_structural_visual_always_displays(self, db_session):
        province = unique("prov")
        make_visual(province=province, name="map", visibility="public", metric=None,
                    data_shape="map_none")
        assert names(displayable_visuals(None, province)) == ["map"]


class TestBuildProvinceMenu:
    def test_menu_config_shape_and_categories(self, db_session):
        province = unique("prov")
        make_visual(province=province, name="v_one", visibility="public",
                    chart_type="bar", slug="v-one", data_types="counts,rates",
                    menu_parent="Deaths", menu_name="Visual One", level="1")
        make_visual(province=province, name="v_two", visibility="public",
                    chart_type="line", menu_parent="Harms", menu_name="Visual Two",
                    level="1")
        menu = build_province_menu(province)
        assert menu["categories"] == ["Deaths", "Harms"]
        entry = menu["config"]["v_one"]
        assert entry["type"] == "bar"
        assert entry["slug"] == "v-one"
        assert entry["data-types"] == ["counts", "rates"]
        assert entry["level"] == 1

    def test_default_prefers_flagged_visual(self, db_session):
        province = unique("prov")
        make_visual(province=province, name="first", visibility="public", level="1")
        make_visual(province=province, name="flagged", visibility="public", level="1",
                    is_default=True)
        assert build_province_menu(province)["default"] == "flagged"

    def test_default_falls_back_to_first_level_one(self, db_session):
        province = unique("prov")
        make_visual(province=province, name="deep", visibility="public", level="2")
        make_visual(province=province, name="shallow", visibility="public", level="1")
        assert build_province_menu(province)["default"] == "shallow"

    def test_default_none_when_empty(self, db_session):
        assert build_province_menu(unique("empty"))["default"] is None

    def test_hidden_visual_absent_from_config_and_data(self, db_session):
        """The single-source guarantee: what the menu hides is not in the payload."""
        from data_viz.visual_generic import build_province_generic
        province = unique("prov")
        make_visual(province=province, name="secret", visibility="private")
        make_visual(province=province, name="open", visibility="public")
        menu = build_province_menu(province)
        data = build_province_generic(province)
        assert "secret" not in menu["config"]
        assert "secret" not in data
        assert "open" in menu["config"]


class TestAccessibleProvinces:
    def test_province_with_public_structural_visual_is_accessible(self, db_session):
        province = unique("prov")
        make_visual(province=province, name="map", visibility="public", metric=None)
        assert province in accessible_provinces(None)

    def test_province_with_only_private_visuals_hidden_from_anonymous(self, db_session):
        province = unique("prov")
        make_visual(province=province, name="priv", visibility="private", metric=None)
        assert province not in accessible_provinces(None)
        admin = make_user(site_admin=True)
        assert province in accessible_provinces(admin)
