from data_viz import das_ingest
from data_viz.database import db
from data_viz.database.models import DasSamples
from tests.factories import make_das_sample


def test_das_replay_ingests_published_runs_up_to_target_in_order(db_session, tmp_path, monkeypatch):
    import datetime as dt
    from data_scraping import orchestrator, storage as storage_mod
    from data_viz.database.models import ScrapeRun
    store = storage_mod.LocalStorage(tmp_path / "bucket")
    runs = []
    for i, until in enumerate([dt.date(2025, 1, 31), dt.date(2025, 2, 28), dt.date(2025, 3, 31)], start=1):
        src = tmp_path / f"{i}.xlsx"
        src.write_bytes(b"x")
        key = storage_mod.object_key("scrapes", "nationalDAS", i, dt.date(2026, 9, 8), until, "xlsx")
        store.put(str(src), key)
        run = ScrapeRun(source_key="nationalDAS", trigger="upload", status="published", s3_key=key,
                        data_until=until, scraped_on=dt.date(2026, 9, 8), is_active=(i == 3))
        db.session.add(run)
        db.session.flush()
        runs.append(run)
    ingested, cleared = [], []
    monkeypatch.setattr("data_viz.das_ingest.clear_das_tables", lambda: cleared.append(True))
    monkeypatch.setattr("data_viz.das_ingest.ingest_das_file",
                        lambda path, scraped, until, commit=True: ingested.append((until, commit)))
    orchestrator.rollback_source("nationalDAS", runs[1].id, None, storage=store)
    assert cleared == [True]
    assert ingested == [(dt.date(2025, 1, 31), False), (dt.date(2025, 2, 28), False)]
    assert runs[1].is_active and not runs[2].is_active


def test_clear_das_tables_empties_rows_without_commit(db_session, monkeypatch):
    calls = []
    make_das_sample()
    monkeypatch.setattr(db.session, "commit", lambda: calls.append("commit"))
    das_ingest.clear_das_tables()
    assert DasSamples.query.count() == 0
    assert calls == []


def test_ingest_das_file_commit_false_leaves_transaction_open(db_session, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(das_ingest, "_parse_workbook", lambda p: ({}, {}, {}, [], []))
    monkeypatch.setattr(db.session, "commit", lambda: calls.append("commit"))
    import datetime
    das_ingest.ingest_das_file(str(tmp_path / "x.xlsx"), datetime.date(2026, 1, 1), datetime.date(2025, 12, 31), commit=False)
    assert calls == []


def test_parse_das_filename():
    import datetime
    assert das_ingest.parse_das_filename("20260908_20250131_nationalDAS.xlsx") == (
        datetime.date(2026, 9, 8), datetime.date(2025, 1, 31))
