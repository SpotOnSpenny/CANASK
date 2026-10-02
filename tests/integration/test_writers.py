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

    def test_options_merges_into_existing_visual_options(self, writer):
        visual, _ = self._visual(visual_options={"time_grains": ["year", "month"],
                                                 "counts-title": "old"})
        vw = writer.visual(visual.province, visual.name)
        vw.options({"counts-title": "Deaths in Ontario"})
        vw.fact("ontario", "2024", 1)
        writer.finish()
        assert visual.visual_options == {"time_grains": ["year", "month"],
                                         "counts-title": "Deaths in Ontario"}

    def test_year_quarter_month_facts_round_trip_in_one_visual(self, writer):
        visual, source = self._visual(visual_options={"time_grains": ["year", "quarter", "month"]})
        vw = writer.visual(visual.province, visual.name)
        vw.fact("ontario", "2025", 12, time_frame_type="year")
        vw.fact("ontario", "2025-Q2", 3, time_frame_type="quarter")
        vw.fact("ontario", "2025-04", 1, time_frame_type="month")
        vw.additional("ontario", "2025-04", "Total Deaths", 9, time_frame_type="month")
        writer.finish()
        got = {(p.time_frame, p.time_frame_type, p.data_type, p.data_value)
               for p in _points_for(source.id)}
        assert got == {("2025", "year", "counts", 12.0),
                       ("2025-Q2", "quarter", "counts", 3.0),
                       ("2025-04", "month", "counts", 1.0),
                       ("2025-04", "month", "additional_rows", 9.0)}


class TestGrainContract:
    """A fact's period key must match its grain, and a visual's grains must be declared before the
    province page could mis-draw them (all checked before finish() touches the DB)."""

    def _vw(self, writer, **kw):
        visual = make_visual(metric="deaths", geo_type="province", data_shape="flat_series",
                             data_source=make_data_source(), **kw)
        return writer.visual(visual.province, visual.name), visual

    @pytest.mark.parametrize("time_frame,grain", [
        ("2025-04", "year"), ("2025", "month"), ("2025-Q2", "month"), ("2025-13", "month"),
        ("2025 Q2", "quarter"), ("2025-Q5", "quarter"), ("2025.0", "year"), ("2025", "week"),
    ])
    def test_key_must_match_its_grain(self, writer, time_frame, grain):
        vw, _ = self._vw(writer)
        with pytest.raises(ValueError, match="period key"):
            vw.fact("ontario", time_frame, 1, time_frame_type=grain)

    def test_additional_row_key_checked_too(self, writer):
        vw, _ = self._vw(writer)
        with pytest.raises(ValueError, match="period key"):
            vw.additional("ontario", "2025-04", "Total Deaths", 9)   # default grain is year

    def test_undeclared_mix_of_grains_refused_before_any_write(self, writer):
        vw, visual = self._vw(writer)
        vw.fact("ontario", "2025", 12)
        vw.fact("ontario", "2025-04", 1, time_frame_type="month")
        with pytest.raises(ValueError, match="declares no visual_options.time_grains"):
            writer.finish()
        assert DataPoints.query.filter_by(data_source_id=visual.data_source_id).count() == 0

    def test_grain_missing_from_declared_list_refused(self, writer):
        vw, _ = self._vw(writer, visual_options={"time_grains": ["year", "quarter"]})
        vw.fact("ontario", "2025", 12)
        vw.additional("ontario", "2025-04", "Total Deaths", 9, time_frame_type="month")
        with pytest.raises(ValueError, match=r"\['month'\] missing from its time_grains"):
            writer.finish()

    def test_single_non_year_grain_needs_no_declaration(self, writer):
        # The month-only drug-checking treemap / expected-vs-actual visuals: one grain, nothing to mix.
        vw, visual = self._vw(writer)
        vw.fact("ontario", "2025-04", 1, time_frame_type="month")
        vw.fact("ontario", "2025-05", 2, time_frame_type="month")
        writer.finish()
        assert DataPoints.query.filter_by(data_source_id=visual.data_source_id).count() == 2

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
        vw.fact("Sask||Old Spelling", "2024-01", 5, time_frame_type="month")
        vw.fact("Sask||Other Site", "2024-01", 7, time_frame_type="month")
        first.finish()

        second = FactWriter(db, MODELS)
        vw = second.visual(visual.province, visual.name)
        vw.use_source({"name": source.name, "link": None, "about": None,
                       "last_updated": None, "data_until": None})
        vw.retire_geo("Sask||Old Spelling")
        vw.fact("Sask||New Spelling", "2024-01", 5, time_frame_type="month")
        second.finish()

        by_geo = {p.geo: p.data_value for p in _points_for(source.id)}
        assert by_geo == {"Sask||New Spelling": 5.0, "Sask||Other Site": 7.0}

        third = FactWriter(db, MODELS)
        vw = third.visual(visual.province, visual.name)
        vw.use_source({"name": source.name, "link": None, "about": None,
                       "last_updated": None, "data_until": None})
        vw.retire_geo("Sask||Old Spelling")   # nothing left to delete -- harmless
        vw.fact("Sask||New Spelling", "2024-01", 6, time_frame_type="month")
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


class TestExportFailsLoudly:
    def test_cleaner_error_propagates_and_keeps_existing_rows(self, db_session, monkeypatch):
        """export_data_to_db skips only a MISSING scrape: any other cleaner error (e.g. the BC
        Coroners month-header guard) must fail the run before finish(), leaving the live rows and
        dropping whatever the run had already buffered."""
        import data_viz.generate_visuals as gv
        source = make_data_source()
        visual = make_visual(metric="deaths", geo_type="province", data_source=source,
                             province=unique("prov"))
        live = DataPoints(data_source_id=source.id, geo_type="province", geo="BC", time_frame="2024",
                          time_frame_type="year", data_metric="deaths", data_type="counts", data_value=5)
        db.session.add(live)
        db.session.flush()

        def failing_builder(writer, province):
            writer.visual(visual.province, visual.name).fact("BC", "2025", 99)
            raise ValueError("BC Coroners sheet 'X': unparseable month header")

        monkeypatch.setattr(gv, "V1_DIRECT", {visual.province: failing_builder})
        with pytest.raises(ValueError, match="unparseable month header"):
            gv.export_data_to_db()
        assert [(p.time_frame, p.data_value) for p in _points_for(source.id)] == [("2024", 5.0)]
