import datetime
import pytest

from data_scraping import storage


D = datetime.date


def test_object_key_layout(monkeypatch):
    monkeypatch.setattr(storage, "_prefix", lambda: "dev/")
    key = storage.object_key("scrapes", "onODPRN", 42, D(2026, 9, 1), D(2026, 7, 31), "xlsx")
    assert key == "dev/scrapes/onODPRN/42_20260901_20260731_onODPRN.xlsx"


def test_materialized_name_strips_run_id():
    assert storage.materialized_name("dev/scrapes/onODPRN/42_20260901_20260731_onODPRN.xlsx") \
        == "20260901_20260731_onODPRN.xlsx"


def test_local_roundtrip_and_copy(tmp_path):
    store = storage.LocalStorage(tmp_path / "bucket")
    src = tmp_path / "a.csv"
    src.write_text("x\n1\n")
    store.put(str(src), "dev/incoming/k/1_20260101_20260101_k.csv")
    store.copy("dev/incoming/k/1_20260101_20260101_k.csv", "dev/scrapes/k/1_20260101_20260101_k.csv")
    out = tmp_path / "out.csv"
    store.get_to("dev/scrapes/k/1_20260101_20260101_k.csv", str(out))
    assert out.read_text() == "x\n1\n"
    assert store.exists("dev/incoming/k/1_20260101_20260101_k.csv")


def test_local_never_overwrites(tmp_path):
    store = storage.LocalStorage(tmp_path / "bucket")
    src = tmp_path / "a.csv"
    src.write_text("1")
    store.put(str(src), "k/x.csv")
    with pytest.raises(FileExistsError):
        store.put(str(src), "k/x.csv")


def test_s3_uses_scrape_credentials_not_global(monkeypatch):
    seen = {}
    monkeypatch.setenv("SCRAPE_STORAGE", "s3")
    monkeypatch.setenv("SCRAPE_S3_BUCKET", "b")
    monkeypatch.setenv("SCRAPE_AWS_ACCESS_KEY_ID", "scrape-id")
    monkeypatch.setenv("SCRAPE_AWS_SECRET_ACCESS_KEY", "scrape-secret")
    monkeypatch.setenv("SCRAPE_AWS_REGION", "ca-central-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ses-id")
    monkeypatch.setattr(storage.boto3, "client", lambda svc, **kw: seen.update(kw) or object())
    s = storage.get_storage()
    assert isinstance(s, storage.S3Storage)
    assert seen["aws_access_key_id"] == "scrape-id"


def test_s3_missing_scrape_credentials_raises_instead_of_falling_back(monkeypatch):
    # Without this guard, boto3.client() would silently fall back to the global AWS_* (SES-only)
    # key when the scrape-specific ones are unset -- a wrong-credential archive/read, not a crash.
    monkeypatch.setenv("SCRAPE_STORAGE", "s3")
    monkeypatch.setenv("SCRAPE_S3_BUCKET", "b")
    monkeypatch.delenv("SCRAPE_AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("SCRAPE_AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ses-id")
    monkeypatch.setattr(storage.boto3, "client", lambda svc, **kw: pytest.fail("boto3.client must not be called"))
    with pytest.raises(RuntimeError, match="SCRAPE_AWS_ACCESS_KEY_ID"):
        storage.get_storage()


def test_s3_missing_bucket_raises(monkeypatch):
    monkeypatch.setenv("SCRAPE_STORAGE", "s3")
    monkeypatch.setenv("SCRAPE_AWS_ACCESS_KEY_ID", "scrape-id")
    monkeypatch.setenv("SCRAPE_AWS_SECRET_ACCESS_KEY", "scrape-secret")
    monkeypatch.delenv("SCRAPE_S3_BUCKET", raising=False)
    monkeypatch.setattr(storage.boto3, "client", lambda svc, **kw: pytest.fail("boto3.client must not be called"))
    with pytest.raises(RuntimeError, match="SCRAPE_S3_BUCKET"):
        storage.get_storage()
