import datetime as dt
import io
import zipfile

import pytest

from data_scraping.context import ActiveMeta, ScrapeContext
from data_scraping.sources import nationalHealthInfobase as nhi


@pytest.mark.parametrize("label, expected", [
    ("2025 Q1", dt.date(2025, 4, 1)), ("2025 Q2", dt.date(2025, 7, 1)),
    ("2025 Q3", dt.date(2025, 10, 1)), ("2025 Q4", dt.date(2026, 1, 1)),
])
def test_data_until_from_quarter(label, expected):
    assert nhi.data_until_from_quarter(label) == expected


def _zip_bytes(csv_text, when=(2026, 6, 11, 0, 0, 0)):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("data.csv", date_time=when), csv_text)
    return buf.getvalue()


CSV = ("Substance,Source,Specific_Measure,Region,PRUID,Time_Period,Year_Quarter,Aggregator,Disaggregator,Unit,Value\n"
       "Opioids,Deaths,Overall numbers,Canada,1,By quarter,2025 Q3,,,Number,100\n")


class FakeDriver:
    def get(self, url): pass
    def find_element(self, *a): return self
    def get_attribute(self, name): return "https://example.org/data.zip"


def _ctx(tmp_path, active=None, monkeypatch=None):
    ctx = ScrapeContext(str(tmp_path), active, None)
    ctx._driver = FakeDriver()
    return ctx


def test_no_new_data_when_zip_not_newer(tmp_path, monkeypatch):
    monkeypatch.setattr(nhi, "_download_link", lambda driver: "https://example.org/data.zip")
    monkeypatch.setattr(nhi, "_fetch", lambda url: _zip_bytes(CSV))
    active = ActiveMeta(data_until=dt.date(2025, 10, 1), scraped_on=dt.date(2026, 6, 11), s3_key="k")
    result = nhi.scrape(_ctx(tmp_path, active))
    assert result.kind == "no_new_data"


def test_new_data_extracts_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(nhi, "_download_link", lambda driver: "https://example.org/data.zip")
    monkeypatch.setattr(nhi, "_fetch", lambda url: _zip_bytes(CSV, when=(2026, 9, 20, 0, 0, 0)))
    active = ActiveMeta(data_until=dt.date(2025, 7, 1), scraped_on=dt.date(2026, 6, 11), s3_key="k")
    result = nhi.scrape(_ctx(tmp_path, active))
    assert result.kind == "new_data"
    assert result.scraped_on == dt.date(2026, 9, 20) and result.data_until == dt.date(2025, 10, 1)
    assert result.path.endswith("_nationalHealthInfobase.csv")
