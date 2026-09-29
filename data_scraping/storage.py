# Scrape archive storage. Every scraped/uploaded file lives here forever (the app key has no delete
# permission); the DB's ScrapeRun.is_active says which object the site is built from. S3 in dev/prod,
# a plain directory for tests and offline dev (SCRAPE_STORAGE=local).

import os
import re
import shutil

import boto3

_RUN_PREFIX = re.compile(r"^\d+_")


def _prefix():
    prefix = os.environ.get("SCRAPE_S3_PREFIX", "dev/")
    return prefix if prefix.endswith("/") or not prefix else prefix + "/"


def object_key(area, source_key, run_id, scraped_on, data_until, ext):
    if area not in ("scrapes", "incoming", "rejected"):
        raise ValueError(f"unknown storage area {area!r}")
    name = f"{run_id}_{scraped_on:%Y%m%d}_{data_until:%Y%m%d}_{source_key}.{ext}"
    return f"{_prefix()}{area}/{source_key}/{name}"


def materialized_name(key):
    """The filename pull_data()/ingest_das expect: <scraped>_<until>_<key>.<ext> (run id dropped)."""
    return _RUN_PREFIX.sub("", os.path.basename(key), count=1)


class LocalStorage:
    def __init__(self, root):
        self.root = str(root)

    def _path(self, key):
        return os.path.join(self.root, *key.split("/"))

    def put(self, local_path, key):
        dest = self._path(key)
        if os.path.exists(dest):
            raise FileExistsError(key)   # mirrors the no-overwrite rule the S3 key enforces by policy
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(local_path, dest)

    def get_to(self, key, local_path):
        shutil.copyfile(self._path(key), local_path)

    def copy(self, src_key, dst_key):
        self.put(self._path(src_key), dst_key)

    def exists(self, key):
        return os.path.exists(self._path(key))


class S3Storage:
    def __init__(self, bucket, client):
        self.bucket = bucket
        self.client = client

    def put(self, local_path, key):
        if self.exists(key):
            raise FileExistsError(key)
        self.client.upload_file(local_path, self.bucket, key)

    def get_to(self, key, local_path):
        self.client.download_file(self.bucket, key, local_path)

    def copy(self, src_key, dst_key):
        if self.exists(dst_key):
            raise FileExistsError(dst_key)
        self.client.copy_object(Bucket=self.bucket, Key=dst_key,
                                CopySource={"Bucket": self.bucket, "Key": src_key})

    def exists(self, key):
        response = self.client.list_objects_v2(Bucket=self.bucket, Prefix=key, MaxKeys=1)
        return any(obj["Key"] == key for obj in response.get("Contents", []))


def get_storage():
    if os.environ.get("SCRAPE_STORAGE", "s3") == "local":
        return LocalStorage(os.environ.get("SCRAPE_LOCAL_DIR", "scrape_archive"))
    # Fail loudly rather than let boto3 silently fall back to the global AWS_* (SES-only) key when
    # the scrape-specific credentials are unset -- that would archive/read scrapes with the wrong
    # (over-privileged, wrong-bucket-scoped) identity instead of the least-privilege canask-scrape one.
    missing = [name for name in ("SCRAPE_AWS_ACCESS_KEY_ID", "SCRAPE_AWS_SECRET_ACCESS_KEY", "SCRAPE_S3_BUCKET")
               if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "SCRAPE_STORAGE=s3 requires " + ", ".join(missing) + " to be set -- without them boto3 "
            "would silently fall back to the global AWS_* (SES-only) credentials.")
    client = boto3.client(
        "s3",
        region_name=os.environ.get("SCRAPE_AWS_REGION"),
        aws_access_key_id=os.environ["SCRAPE_AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["SCRAPE_AWS_SECRET_ACCESS_KEY"],
    )
    return S3Storage(os.environ["SCRAPE_S3_BUCKET"], client)
