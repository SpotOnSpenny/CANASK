"""sync_visual_definitions against a tmp manifest dir (the one pipeline path with a
directory parameter). Covers create/update/prune and the owner-managed fields that
must survive a re-sync."""
import json

import pytest

from data_viz.database import db
from data_viz.database.models import (DataSources, GroupDataSources, GroupLandingVisuals,
                                      GroupVisuals, UserActivity, Visuals, VisualQuery)
from data_viz.visual_definitions import sync_visual_definitions

from tests.factories import make_group, set_landing, unique


def hold_synced_rows():
    """Before a sync that is expected to FAIL after an earlier one succeeded: a failed sync ends
    in db.session.rollback(), which must land on a SAVEPOINT rather than on the db_session
    fixture's outer transaction (that would wipe the rows the earlier sync wrote, and let later
    commits escape the test). Commit, then open a savepoint on every bound connection."""
    db.session.commit()
    for connection in db.engines.values():
        connection.begin_nested()


def write_manifest(tmp_path, source_name, visuals, filename="test-source.json"):
    manifest = {"data_source": {"name": source_name, "link": "https://example.test"},
                "visuals": visuals}
    (tmp_path / filename).write_text(json.dumps(manifest))
    return tmp_path


def entry(province, visual_id, **overrides):
    base = {
        "province": province, "visual_id": visual_id, "shape": "flat_series",
        "metric": "deaths", "dimension2_type": "drug_type", "key_kind": "plain",
        "chart_type": "bar", "data_types": ["counts"], "menu_parent": "Deaths",
        "menu_name": visual_id, "level": 1, "vis_parent": None, "next_vis": None,
        "is_default": False,
    }
    base.update(overrides)
    return base


@pytest.fixture()
def province():
    return unique("manifprov")


class TestCreate:
    def test_creates_visual_with_derived_state(self, db_session, tmp_path, province):
        write_manifest(tmp_path, unique("src"), [entry(province, "deaths_by_drug")])
        counts = sync_visual_definitions(manifest_dir=str(tmp_path))
        assert counts["created"] == 1
        visual = Visuals.query.filter_by(province=province, name="deaths_by_drug").one()
        assert visual.data_shape == "flat_series"
        assert visual.drill_chain == ["dimension2"]       # derived, not authored
        assert visual.slug == "deaths-by-drug"            # derived from visual_id
        assert visual.data_types == "counts"
        assert visual.level == "1"                        # stored as string
        assert visual.visibility == "private"             # sourced visuals seed private
        assert visual.data_source_id is not None

    def test_sourceless_manifest_seeds_public(self, db_session, tmp_path, province):
        manifest = {"data_source": None,
                    "visuals": [entry(province, "scaffold_map", shape="map_none",
                                      metric=None)]}
        (tmp_path / "scaffolding.json").write_text(json.dumps(manifest))
        sync_visual_definitions(manifest_dir=str(tmp_path))
        visual = Visuals.query.filter_by(province=province, name="scaffold_map").one()
        assert visual.visibility == "public"
        assert visual.data_source_id is None


class TestUpdate:
    def test_update_in_place_keeps_id(self, db_session, tmp_path, province):
        write_manifest(tmp_path, unique("src"), [entry(province, "v")])
        sync_visual_definitions(manifest_dir=str(tmp_path))
        visual_id = Visuals.query.filter_by(province=province, name="v").one().id

        write_manifest(tmp_path, unique("src2"), [entry(province, "v", chart_type="line")])
        counts = sync_visual_definitions(manifest_dir=str(tmp_path))
        assert counts == {"created": 0, "updated": 1, "pruned": 0}
        visual = Visuals.query.filter_by(province=province, name="v").one()
        assert visual.id == visual_id
        assert visual.chart_type == "line"

    def test_resync_preserves_owner_managed_fields(self, db_session, tmp_path, province):
        """visibility + is_default are UI-owned after creation; grants persist because
        the id is stable."""
        write_manifest(tmp_path, unique("src"), [entry(province, "v")])
        sync_visual_definitions(manifest_dir=str(tmp_path))
        visual = Visuals.query.filter_by(province=province, name="v").one()
        visual.visibility = "public"
        visual.is_default = True
        group = make_group()
        db.session.add(GroupVisuals(group_id=group.id, visual_id=visual.id))
        db.session.flush()

        sync_visual_definitions(manifest_dir=str(tmp_path))
        visual = Visuals.query.filter_by(province=province, name="v").one()
        assert visual.visibility == "public"
        assert visual.is_default is True
        assert GroupVisuals.query.filter_by(visual_id=visual.id).count() == 1

    def test_resync_preserves_gen_time_visual_options_when_manifest_silent(
            self, db_session, tmp_path, province):
        write_manifest(tmp_path, unique("src"), [entry(province, "v")])
        sync_visual_definitions(manifest_dir=str(tmp_path))
        visual = Visuals.query.filter_by(province=province, name="v").one()
        visual.visual_options = {"counts-title": "set at gen time"}
        db.session.flush()

        sync_visual_definitions(manifest_dir=str(tmp_path))
        assert Visuals.query.filter_by(province=province, name="v").one().visual_options \
            == {"counts-title": "set at gen time"}


class TestPrune:
    def test_removed_entry_pruned_with_children_rows(self, db_session, tmp_path, province):
        source = unique("src")
        write_manifest(tmp_path, source, [entry(province, "keep"), entry(province, "drop")])
        sync_visual_definitions(manifest_dir=str(tmp_path))
        dropped = Visuals.query.filter_by(province=province, name="drop").one()
        group = make_group()
        db.session.add(GroupVisuals(group_id=group.id, visual_id=dropped.id))
        db.session.add(VisualQuery(filter_type="geo", filter_value=province,
                                   for_visual_id=dropped.id))
        db.session.flush()

        write_manifest(tmp_path, source, [entry(province, "keep")])
        counts = sync_visual_definitions(manifest_dir=str(tmp_path))
        assert counts["pruned"] == 1
        assert Visuals.query.filter_by(province=province, name="drop").count() == 0
        assert GroupVisuals.query.filter_by(visual_id=dropped.id).count() == 0
        assert VisualQuery.query.filter_by(for_visual_id=dropped.id).count() == 0
        assert Visuals.query.filter_by(province=province, name="keep").count() == 1

    def test_pruned_visual_takes_its_opening_view_picks_with_it(self, db_session, tmp_path,
                                                                province):
        """A group that picked the visual as its starting chart must not block the prune (a
        foreign key into visuals) nor keep a row pointing at nothing."""
        source = unique("src")
        write_manifest(tmp_path, source, [entry(province, "keep"), entry(province, "drop")])
        sync_visual_definitions(manifest_dir=str(tmp_path))
        dropped = Visuals.query.filter_by(province=province, name="drop").one()
        kept = Visuals.query.filter_by(province=province, name="keep").one()
        leaving, staying = make_group(), make_group()
        set_landing(leaving, dropped)
        set_landing(staying, kept)

        write_manifest(tmp_path, source, [entry(province, "keep")])
        assert sync_visual_definitions(manifest_dir=str(tmp_path))["pruned"] == 1
        db.session.expire_all()
        assert GroupLandingVisuals.query.filter_by(group_id=leaving.id).count() == 0
        assert GroupLandingVisuals.query.filter_by(group_id=staying.id).count() == 1

    def test_prune_scoped_to_own_source(self, db_session, tmp_path, province):
        """A manifest prunes only within its source's scope - other sources' visuals
        are untouched even when absent from this manifest."""
        write_manifest(tmp_path, unique("src-a"), [entry(province, "a_vis")], "a.json")
        write_manifest(tmp_path, unique("src-b"), [entry(province, "b_vis")], "b.json")
        sync_visual_definitions(manifest_dir=str(tmp_path))
        assert Visuals.query.filter_by(province=province).count() == 2

        # Re-sync only source A's manifest dir (B's file gone entirely): B's file absence
        # means no scope for B, so b_vis must survive.
        (tmp_path / "b.json").unlink()
        sync_visual_definitions(manifest_dir=str(tmp_path))
        assert Visuals.query.filter_by(province=province, name="b_vis").count() == 1

    def test_empty_dir_is_a_noop(self, db_session, tmp_path):
        assert sync_visual_definitions(manifest_dir=str(tmp_path)) == {
            "created": 0, "updated": 0, "pruned": 0}


class TestInvalidOptions:
    def test_malformed_time_grains_fails_the_sync_and_writes_nothing(self, db_session, tmp_path, province):
        write_manifest(tmp_path, unique("src"), [
            entry(province, "ok_visual"),
            entry(province, "bad_visual", visual_options={"time_grains": "quarterly"}),
        ])
        with pytest.raises(ValueError, match=f"{province}/bad_visual: time_grains"):
            sync_visual_definitions(manifest_dir=str(tmp_path))
        assert Visuals.query.filter_by(province=province).count() == 0


def write_source_manifest(tmp_path, data_source, province, filename="test-source.json"):
    """A one-visual manifest with a caller-supplied `data_source` block."""
    (tmp_path / filename).write_text(json.dumps(
        {"data_source": data_source, "visuals": [entry(province, "a_visual")]}))
    return tmp_path


class TestRestrictedFlag:
    """`"restricted": true` in a manifest's data_source is the ONLY switch for a restricted
    source (auth/restricted.py) -- define-visuals syncs it onto the DataSources row."""

    def _restriction_activity(self, source):
        return UserActivity.query.filter_by(activity_type="source_restriction_changed",
                                            activity_target_id=source.id).all()

    def test_absent_key_means_unrestricted(self, db_session, tmp_path, province):
        name = unique("src")
        write_source_manifest(tmp_path, {"name": name}, province)
        sync_visual_definitions(manifest_dir=str(tmp_path))
        source = DataSources.query.filter_by(name=name).one()
        assert source.restricted is False
        assert self._restriction_activity(source) == []

    def test_manifest_turns_the_flag_on_and_logs_it(self, db_session, tmp_path, province):
        name = unique("src")
        write_source_manifest(tmp_path, {"name": name, "restricted": True}, province)
        sync_visual_definitions(manifest_dir=str(tmp_path))
        source = DataSources.query.filter_by(name=name).one()
        assert source.restricted is True
        (activity,) = self._restriction_activity(source)
        assert name in activity.details and "restricted" in activity.details

    def test_resync_keeps_the_flag_without_logging_again(self, db_session, tmp_path, province):
        name = unique("src")
        write_source_manifest(tmp_path, {"name": name, "restricted": True}, province)
        sync_visual_definitions(manifest_dir=str(tmp_path))
        sync_visual_definitions(manifest_dir=str(tmp_path))
        source = DataSources.query.filter_by(name=name).one()
        assert source.restricted is True
        assert len(self._restriction_activity(source)) == 1

    def test_existing_unrestricted_source_becomes_restricted(self, db_session, tmp_path,
                                                             province):
        name = unique("src")
        write_source_manifest(tmp_path, {"name": name}, province)
        sync_visual_definitions(manifest_dir=str(tmp_path))
        source = DataSources.query.filter_by(name=name).one()
        write_source_manifest(tmp_path, {"name": name, "restricted": True}, province)
        sync_visual_definitions(manifest_dir=str(tmp_path))
        assert source.restricted is True
        assert DataSources.query.filter_by(name=name).one().id == source.id   # same row

    def test_removing_the_key_lifts_the_restriction_and_logs_it(self, db_session, tmp_path,
                                                                province):
        name = unique("src")
        write_source_manifest(tmp_path, {"name": name, "restricted": True}, province)
        sync_visual_definitions(manifest_dir=str(tmp_path))
        write_source_manifest(tmp_path, {"name": name}, province)
        sync_visual_definitions(manifest_dir=str(tmp_path))
        source = DataSources.query.filter_by(name=name).one()
        assert source.restricted is False
        assert len(self._restriction_activity(source)) == 2

    @pytest.mark.parametrize("value", ["true", 1, "yes", None])
    def test_non_boolean_fails_the_sync(self, db_session, tmp_path, province, value):
        name = unique("src")
        write_source_manifest(tmp_path, {"name": name, "restricted": value}, province)
        with pytest.raises(ValueError, match="restricted"):
            sync_visual_definitions(manifest_dir=str(tmp_path))
        assert DataSources.query.filter_by(name=name).count() == 0

    def test_misspelled_key_fails_the_sync_instead_of_failing_open(self, db_session, tmp_path,
                                                                   province):
        name = unique("src")
        write_source_manifest(tmp_path, {"name": name, "restriced": True}, province)
        with pytest.raises(ValueError, match="restriced"):
            sync_visual_definitions(manifest_dir=str(tmp_path))
        assert DataSources.query.filter_by(name=name).count() == 0

    def test_two_manifests_disagreeing_about_one_source_fail_the_sync(self, db_session,
                                                                      tmp_path, province):
        name = unique("src")
        write_source_manifest(tmp_path, {"name": name, "restricted": True}, province,
                              filename="a.json")
        (tmp_path / "b.json").write_text(json.dumps(
            {"data_source": {"name": name}, "visuals": [entry(province, "other_visual")]}))
        with pytest.raises(ValueError, match="disagree"):
            sync_visual_definitions(manifest_dir=str(tmp_path))

    def test_disagreeing_manifests_leave_an_existing_restricted_row_alone(self, db_session,
                                                                          tmp_path, province):
        """The failed sync must not half-apply: the alphabetically first file (no flag) is
        upserted -- un-restricting the row in the session -- before the second file is seen to
        disagree, and the rollback has to undo that along with its audit row."""
        name = unique("src")
        write_source_manifest(tmp_path, {"name": name, "restricted": True}, province,
                              filename="b.json")
        sync_visual_definitions(manifest_dir=str(tmp_path))
        hold_synced_rows()
        (tmp_path / "a.json").write_text(json.dumps(
            {"data_source": {"name": name}, "visuals": [entry(province, "other_visual")]}))
        with pytest.raises(ValueError, match="disagree"):
            sync_visual_definitions(manifest_dir=str(tmp_path))
        source = DataSources.query.filter_by(name=name).one()
        assert source.restricted is True
        assert len(self._restriction_activity(source)) == 1   # only the original switch-on
        assert Visuals.query.filter_by(province=province, name="other_visual").count() == 0

    def test_now_restricted_is_printed_only_once_the_sync_has_committed(self, db_session,
                                                                        tmp_path, province,
                                                                        capsys):
        # A deploy log that says "is now restricted" for a sync that then rolled back would be
        # wrong about access control.
        name = unique("src")
        write_source_manifest(tmp_path, {"name": name}, province, filename="a.json")
        sync_visual_definitions(manifest_dir=str(tmp_path))
        hold_synced_rows()
        write_source_manifest(tmp_path, {"name": name, "restricted": True}, province,
                              filename="a.json")
        (tmp_path / "b.json").write_text(json.dumps(
            {"data_sources": {"name": unique("src")}, "visuals": []}))   # invalid: fails the sync
        capsys.readouterr()
        with pytest.raises(ValueError, match="b.json"):
            sync_visual_definitions(manifest_dir=str(tmp_path))
        assert "is now restricted" not in capsys.readouterr().out
        source = DataSources.query.filter_by(name=name).one()
        assert source.restricted is False
        assert self._restriction_activity(source) == []

        (tmp_path / "b.json").unlink()
        sync_visual_definitions(manifest_dir=str(tmp_path))
        assert f"Data source {name!r} is now restricted" in capsys.readouterr().out
        assert DataSources.query.filter_by(name=name).one().restricted is True

    def test_restricted_source_no_group_holds_is_warned_about(self, db_session, tmp_path,
                                                              province, capsys):
        # Dark for everyone, and nothing in the app can attach it: the sync says how to fix it.
        name = unique("src")
        write_source_manifest(tmp_path, {"name": name, "restricted": True}, province)
        sync_visual_definitions(manifest_dir=str(tmp_path))
        (warning,) = [line for line in capsys.readouterr().out.splitlines()
                      if line.startswith("WARNING")]
        assert name in warning and "set-group-source" in warning

        source = DataSources.query.filter_by(name=name).one()
        db.session.add(GroupDataSources(group_id=make_group().id, data_source_id=source.id))
        db.session.flush()
        sync_visual_definitions(manifest_dir=str(tmp_path))
        assert "WARNING" not in capsys.readouterr().out

    def test_open_source_no_group_holds_is_not_warned_about(self, db_session, tmp_path,
                                                            province, capsys):
        write_source_manifest(tmp_path, {"name": unique("src")}, province)
        sync_visual_definitions(manifest_dir=str(tmp_path))
        assert "WARNING" not in capsys.readouterr().out


class TestManifestTopLevel:
    """A manifest has exactly `data_source` and `visuals` (optionally `_comment`). A misspelled
    or missing `data_source` would otherwise read as a second sourceless manifest: its visuals
    detached from their source, and so from the source's `restricted` flag."""

    def test_unknown_top_level_key_fails_the_sync(self, db_session, tmp_path, province):
        name = unique("src")
        (tmp_path / "typo.json").write_text(json.dumps(
            {"data_sources": {"name": name, "restricted": True},
             "visuals": [entry(province, "a_visual")]}))
        with pytest.raises(ValueError, match="typo.json.*data_sources"):
            sync_visual_definitions(manifest_dir=str(tmp_path))
        assert Visuals.query.filter_by(province=province).count() == 0
        assert DataSources.query.filter_by(name=name).count() == 0

    def test_missing_data_source_key_fails_the_sync(self, db_session, tmp_path, province):
        (tmp_path / "bare.json").write_text(json.dumps(
            {"visuals": [entry(province, "a_visual")]}))
        with pytest.raises(ValueError, match='missing "data_source"'):
            sync_visual_definitions(manifest_dir=str(tmp_path))
        assert Visuals.query.filter_by(province=province).count() == 0

    def test_restricted_written_one_level_too_high_fails_the_sync(self, db_session, tmp_path,
                                                                  province):
        name = unique("src")
        (tmp_path / "high.json").write_text(json.dumps(
            {"restricted": True, "data_source": {"name": name},
             "visuals": [entry(province, "a_visual")]}))
        with pytest.raises(ValueError, match="restricted"):
            sync_visual_definitions(manifest_dir=str(tmp_path))
        assert DataSources.query.filter_by(name=name).count() == 0

    def test_two_sourceless_manifests_fail_the_sync(self, db_session, tmp_path, province):
        # Each would be "the full desired state" of the sourceless scope and prune the other's.
        for filename, visual_id in (("a.json", "map_a"), ("b.json", "map_b")):
            (tmp_path / filename).write_text(json.dumps(
                {"data_source": None,
                 "visuals": [entry(province, visual_id, shape="map_none", metric=None)]}))
        with pytest.raises(ValueError, match="b.json.*only one manifest may have a null data_source"):
            sync_visual_definitions(manifest_dir=str(tmp_path))
        assert Visuals.query.filter_by(province=province).count() == 0
