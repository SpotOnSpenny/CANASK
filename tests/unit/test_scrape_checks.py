import datetime

from data_scraping import checks
from data_scraping.checks import ActiveVersion
from tests.scrape_fixtures import FAKE_CONTRACT, fake_spec, good_rows, write_csv

D = datetime.date


def levels(outcome, level):
    return [r for r in outcome.results if r.level == level]


class TestTier1:
    def test_good_file_passes(self, tmp_path):
        out = checks.tier1(write_csv(tmp_path / "f.csv", good_rows()), FAKE_CONTRACT)
        assert out.ok and not levels(out, "fail")

    def test_missing_column_rejects(self, tmp_path):
        rows = [{"Region": "Alberta", "Year": "2024"}]
        out = checks.tier1(write_csv(tmp_path / "f.csv", rows), FAKE_CONTRACT)
        assert not out.ok
        assert "Value" in levels(out, "fail")[0].message

    def test_extra_column_is_notice_not_failure(self, tmp_path):
        rows = [dict(r, Sex="Male") for r in good_rows()]
        out = checks.tier1(write_csv(tmp_path / "f.csv", rows), FAKE_CONTRACT)
        assert out.ok
        assert any("Sex" in n.message for n in levels(out, "notice"))

    def test_rename_hint(self, tmp_path):
        rows = [{"Region": r["Region"], "Year": r["Year"], "Values": r["Value"]} for r in good_rows()]
        out = checks.tier1(write_csv(tmp_path / "f.csv", rows), FAKE_CONTRACT)
        assert not out.ok
        assert "did it become 'Values'" in levels(out, "fail")[0].message

    def test_new_dimension_value_is_notice(self, tmp_path):
        rows = good_rows() + [{"Region": "Yukon", "Year": "2024", "Value": 1.0}]
        out = checks.tier1(write_csv(tmp_path / "f.csv", rows), FAKE_CONTRACT)
        assert out.ok
        assert any("Yukon" in n.message for n in levels(out, "notice"))

    def test_number_column_allows_markers_but_not_junk(self, tmp_path):
        ok = checks.tier1(write_csv(tmp_path / "a.csv", good_rows() + [{"Region": "Ontario", "Year": "2022", "Value": "Suppr."}]), FAKE_CONTRACT)
        assert ok.ok
        junk = [dict(r, Value="abc") for r in good_rows()]
        assert not checks.tier1(write_csv(tmp_path / "b.csv", junk), FAKE_CONTRACT).ok

    def test_wrong_extension_rejects(self, tmp_path):
        p = tmp_path / "f.xlsx"
        p.write_bytes(b"not really")
        assert not checks.tier1(str(p), FAKE_CONTRACT).ok

    def test_custom_validator(self, tmp_path):
        from data_scraping.contracts import FileContract

        def boom(path):
            raise ValueError("ID All: no header matching 'Sample #'")
        p = tmp_path / "f.xlsx"
        p.write_bytes(b"x")
        out = checks.tier1(str(p), FileContract(ext="xlsx", custom=boom))
        assert not out.ok and "Sample #" in levels(out, "fail")[0].message

    def test_custom_validator_garbage_file_rejects_not_crashes(self, tmp_path):
        from data_scraping import registry

        p = tmp_path / "garbage.xlsx"
        p.write_bytes(b"this is not a real xlsx file")
        out = checks.tier1(str(p), registry.get_source("nationalDAS").contract)
        assert not out.ok
        assert len(levels(out, "fail")) == 1


class TestTier2:
    def _active(self, tmp_path, rows, until=D(2024, 12, 31)):
        p = write_csv(tmp_path / "active.csv", rows)
        return ActiveVersion(path=p, data_until=until, content_hash=checks.file_hash(p))

    def test_first_run_is_ok(self, tmp_path):
        p = write_csv(tmp_path / "n.csv", good_rows())
        out = checks.tier2(fake_spec(), p, D(2024, 12, 31), checks.file_hash(p), None)
        assert out.decision == "ok"

    def test_identical_content_is_no_new_data(self, tmp_path):
        active = self._active(tmp_path, good_rows())
        out = checks.tier2(fake_spec(), active.path, active.data_until, active.content_hash, active)
        assert out.decision == "no_new_data"

    def test_data_until_backwards_holds(self, tmp_path):
        active = self._active(tmp_path, good_rows())
        p = write_csv(tmp_path / "n.csv", good_rows(extra_year="2025"))
        out = checks.tier2(fake_spec(), p, D(2023, 1, 1), checks.file_hash(p), active)
        assert out.decision == "hold"

    def test_row_drop_holds(self, tmp_path):
        active = self._active(tmp_path, good_rows(extra_year="2025"))
        p = write_csv(tmp_path / "n.csv", good_rows()[:2])
        out = checks.tier2(fake_spec(), p, D(2025, 12, 31), checks.file_hash(p), active)
        assert out.decision == "hold"
        assert any(r.check == "row_count" and r.level == "fail" for r in out.results)

    def test_settled_revision_warns_but_publishes(self, tmp_path):
        active = self._active(tmp_path, good_rows())
        revised = [dict(r, Value=99.0) if r["Year"] == "2023" else r for r in good_rows(extra_year="2025")]
        p = write_csv(tmp_path / "n.csv", revised)
        out = checks.tier2(fake_spec(), p, D(2025, 12, 31), checks.file_hash(p), active)
        assert out.decision == "ok"
        assert any(r.check == "settled_revisions" and r.level == "warn" for r in out.results)

    def test_das_kind_skips_row_and_revision_checks(self, tmp_path):
        active = self._active(tmp_path, good_rows(extra_year="2025"))
        p = write_csv(tmp_path / "n.csv", good_rows()[:1])
        out = checks.tier2(fake_spec(kind="das"), p, D(2025, 12, 31), checks.file_hash(p), active)
        assert out.decision == "ok"
