"""FactWriter/VisualWriter driven directly against hand-built Visuals rows - the
fact-emission + predicate contract the cleaners rely on, with no output/ files."""
import pytest

from data_viz.database import db
from data_viz.database.models import DataPoints, DataSources, VisualQuery, Visuals
from data_viz.generate_visuals import SUPPRESSED, FactWriter

from tests.factories import make_data_source, make_visual, unique

MODELS = (DataSources, DataPoints, Visuals, VisualQuery)


@pytest.fixture()
def writer(db_session):
    return FactWriter(db, MODELS)


def _points_for(source_id):
    return DataPoints.query.filter_by(data_source_id=source_id).all()


class TestUpsertSource:
    def test_creates_by_name_and_refreshes_metadata(self, writer):
        name = unique("src")
        source_id = writer.upsert_source(
            {"name": name, "link": "https://x", "about": "blurb",
             "last_updated": "July 1, 2026", "data_until": "June 30, 2026"})
        row = db.session.get(DataSources, source_id)
        assert (row.link, row.about) == ("https://x", "blurb")
        assert row.last_updated_str == "July 1, 2026"

    def test_same_name_reuses_row(self, writer):
        name = unique("src")
        first = writer.upsert_source({"name": name, "about": "v1"})
        second = writer.upsert_source({"name": name, "about": "v2"})
        assert first == second
        assert db.session.get(DataSources, first).about == "v2"


class TestVisualWriter:
    def _visual(self, **kw):
        source = make_data_source()
        defaults = dict(metric="deaths", geo_type="province", data_shape="flat_series",
                        data_source=source)
        defaults.update(kw)
        return make_visual(**defaults), source

    def test_fact_emits_point_and_geo_predicate(self, writer):
        visual, source = self._visual()
        vw = writer.visual(visual.province, visual.name)
        vw.fact("ontario", "2024", 47)
        writer.finish()
        (point,) = _points_for(source.id)
        assert (point.geo, point.time_frame, point.data_metric) == ("ontario", "2024", "deaths")
        assert point.data_value == 47.0
        preds = VisualQuery.query.filter_by(for_visual_id=visual.id).all()
        assert [(p.filter_type, p.filter_value) for p in preds] == [("geo", "ontario")]

    def test_non_province_geo_type_emits_no_geo_predicate(self, writer):
        visual, source = self._visual(geo_type="health_authority")
        vw = writer.visual(visual.province, visual.name)
        vw.fact("Fraser", "2024", 5)
        writer.finish()
        assert VisualQuery.query.filter_by(for_visual_id=visual.id).count() == 0

    def test_dimension_value_recorded_as_predicate(self, writer):
        visual, source = self._visual()
        vw = writer.visual(visual.province, visual.name)
        vw.fact("ontario", "2024", 10, dimension="opioids")
        writer.finish()
        preds = {(p.filter_type, p.filter_value)
                 for p in VisualQuery.query.filter_by(for_visual_id=visual.id)}
        assert ("dimension", "opioids") in preds
        (point,) = _points_for(source.id)
        # Untyped manifest slot defaults the dimension type to "substance".
        assert (point.dimension_type, point.dimension_value) == ("substance", "opioids")

    def test_suppressed_round_trips_through_text_column(self, writer):
        visual, source = self._visual()
        vw = writer.visual(visual.province, visual.name)
        vw.fact("ontario", "2024", SUPPRESSED)
        writer.finish()
        (point,) = _points_for(source.id)
        assert point.data_value is None
        assert point.data_value_text == SUPPRESSED

    def test_duplicate_natural_key_dedups_within_run(self, writer):
        visual, source = self._visual()
        vw = writer.visual(visual.province, visual.name)
        vw.fact("ontario", "2024", 1)
        vw.fact("ontario", "2024", 999)  # same key: first buffered row wins
        writer.finish()
        (point,) = _points_for(source.id)
        assert point.data_value == 1.0

    def test_additional_row(self, writer):
        visual, source = self._visual()
        vw = writer.visual(visual.province, visual.name)
        vw.additional("ontario", "2024", "Total Deaths", 123)
        writer.finish()
        (point,) = _points_for(source.id)
        assert (point.data_metric, point.data_type) == ("total_deaths", "additional_rows")
        assert point.dimension_value == "Total Deaths"
        preds = {(p.filter_type, p.filter_value)
                 for p in VisualQuery.query.filter_by(for_visual_id=visual.id)}
        assert ("additional_metric", "total_deaths") in preds

    def test_undefined_visual_returns_none(self, writer, capsys):
        assert writer.visual("narnia", "no_such_visual") is None
        assert "define-visuals" in capsys.readouterr().out

    def test_options_committed_by_finish(self, writer):
        visual, _ = self._visual()
        vw = writer.visual(visual.province, visual.name)
        vw.options({"counts-title": "Deaths in Ontario"})
        vw.fact("ontario", "2024", 1)
        writer.finish()
        assert visual.visual_options == {"counts-title": "Deaths in Ontario"}


class TestScopedRewrite:
    def test_finish_replaces_only_reproduced_territory(self, db_session):
        """A re-run for geo A must not clobber geo B's rows or another source's rows."""
        source_a, source_b = make_data_source(), make_data_source()
        visual = make_visual(metric="deaths", geo_type="province",
                            data_source=source_a, province=unique("prov"))

        first = FactWriter(db, MODELS)
        vw = first.visual(visual.province, visual.name)
        vw.fact("ontario", "2024", 1)
        vw.fact("quebec", "2024", 2)
        first.finish()
        # Unrelated source's row, must survive any later run.
        other = FactWriter(db, MODELS)
        other.point(source_b.id, "province", "ontario", "2024", "samples", "counts", value=9)
        other.finish()

        second = FactWriter(db, MODELS)
        vw = second.visual(visual.province, visual.name)
        vw.fact("ontario", "2024", 100)  # reproduces ontario only
        second.finish()

        by_geo = {(p.geo): p.data_value for p in _points_for(source_a.id)}
        assert by_geo == {"ontario": 100.0, "quebec": 2.0}
        assert [p.data_value for p in _points_for(source_b.id)] == [9.0]

    def test_retire_geo_deletes_stale_rows_without_replacement(self, db_session):
        """A renamed site's old-spelling rows are never re-emitted, so retire_geo must claim
        them for deletion -- and leave every other geo alone. Idempotent on a second run."""
        source = make_data_source()
        visual = make_visual(metric="samples", geo_type="site",
                             data_source=source, province=unique("prov"))
        first = FactWriter(db, MODELS)
        vw = first.visual(visual.province, visual.name)
        vw.fact("Sask||Old Spelling", "2024-01", 5)
        vw.fact("Sask||Other Site", "2024-01", 7)
        first.finish()

        second = FactWriter(db, MODELS)
        vw = second.visual(visual.province, visual.name)
        vw.use_source({"name": source.name, "link": None, "about": None,
                       "last_updated": None, "data_until": None})
        vw.retire_geo("Sask||Old Spelling")
        vw.fact("Sask||New Spelling", "2024-01", 5)
        second.finish()

        by_geo = {p.geo: p.data_value for p in _points_for(source.id)}
        assert by_geo == {"Sask||New Spelling": 5.0, "Sask||Other Site": 7.0}

        third = FactWriter(db, MODELS)
        vw = third.visual(visual.province, visual.name)
        vw.use_source({"name": source.name, "link": None, "about": None,
                       "last_updated": None, "data_until": None})
        vw.retire_geo("Sask||Old Spelling")   # nothing left to delete -- harmless
        vw.fact("Sask||New Spelling", "2024-01", 6)
        third.finish()
        by_geo = {p.geo: p.data_value for p in _points_for(source.id)}
        assert by_geo == {"Sask||New Spelling": 6.0, "Sask||Other Site": 7.0}

    def test_finish_refreshes_touched_visuals_predicates(self, db_session):
        source = make_data_source()
        visual = make_visual(metric="deaths", geo_type="province",
                            data_source=source, province=unique("prov"))
        first = FactWriter(db, MODELS)
        first.visual(visual.province, visual.name).fact("ontario", "2024", 1, dimension="opioids")
        first.finish()

        second = FactWriter(db, MODELS)
        second.visual(visual.province, visual.name).fact("ontario", "2024", 2, dimension="stimulants")
        second.finish()

        preds = {(p.filter_type, p.filter_value)
                 for p in VisualQuery.query.filter_by(for_visual_id=visual.id)}
        # Old dimension predicate replaced, not accumulated.
        assert preds == {("geo", "ontario"), ("dimension", "stimulants")}
