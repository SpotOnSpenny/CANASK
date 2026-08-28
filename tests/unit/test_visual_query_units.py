"""Pure access-control and value-reconstruction helpers, driven with plain stubs."""
from types import SimpleNamespace

from data_viz.visual_query import _can_see, _prune_orphans, _value


def visual(id=1, visibility="public", data_source_id=None, name="v", vis_parent_name=None):
    return SimpleNamespace(id=id, visibility=visibility, data_source_id=data_source_id,
                           name=name, vis_parent_name=vis_parent_name)


class TestCanSee:
    def test_public_visible_to_anonymous(self):
        assert _can_see(visual(visibility="public"), False, False, set(), set()) is True

    def test_non_public_hidden_from_anonymous(self):
        assert _can_see(visual(visibility="group"), False, False, set(), set()) is False
        assert _can_see(visual(visibility="private"), False, False, set(), set()) is False

    def test_site_admin_sees_everything(self):
        assert _can_see(visual(visibility="private"), True, True, set(), set()) is True

    def test_source_owner_sees_private(self):
        v = visual(visibility="private", data_source_id=7)
        assert _can_see(v, True, False, {7}, set()) is True
        assert _can_see(v, True, False, {8}, set()) is False

    def test_group_visibility_needs_grant(self):
        v = visual(id=42, visibility="group")
        assert _can_see(v, True, False, set(), {42}) is True
        assert _can_see(v, True, False, set(), {41}) is False

    def test_unrecognized_visibility_fails_closed(self):
        assert _can_see(visual(visibility="banana"), True, False, set(), set()) is False


class TestPruneOrphans:
    def test_child_without_kept_parent_pruned(self):
        parent = visual(id=1, name="parent")
        child = visual(id=2, name="child", vis_parent_name="parent")
        kept = _prune_orphans([parent, child], {2})
        assert kept == set()

    def test_child_with_kept_parent_survives(self):
        parent = visual(id=1, name="parent")
        child = visual(id=2, name="child", vis_parent_name="parent")
        assert _prune_orphans([parent, child], {1, 2}) == {1, 2}

    def test_prune_cascades_down_the_chain(self):
        a = visual(id=1, name="a")
        b = visual(id=2, name="b", vis_parent_name="a")
        c = visual(id=3, name="c", vis_parent_name="b")
        # a hidden -> b orphaned -> c orphaned, even though c's direct parent was kept initially
        assert _prune_orphans([a, b, c], {2, 3}) == set()

    def test_missing_parent_row_prunes_child(self):
        child = visual(id=2, name="child", vis_parent_name="ghost")
        assert _prune_orphans([child], {2}) == set()


class TestValue:
    def point(self, value=None, text=None):
        return SimpleNamespace(data_value=value, data_value_text=text)

    def test_text_round_trips_verbatim(self):
        assert _value(self.point(value=47.0, text="47")) == "47"

    def test_integer_float_becomes_int(self):
        assert _value(self.point(value=47.0)) == 47
        assert isinstance(_value(self.point(value=47.0)), int)

    def test_fractional_stays_float(self):
        assert _value(self.point(value=4.7)) == 4.7

    def test_none(self):
        assert _value(self.point()) is None
