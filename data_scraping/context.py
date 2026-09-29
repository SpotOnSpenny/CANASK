# What a scraper gets (ScrapeContext) and returns (ScrapeResult). Scrapers write into work_dir only;
# the orchestrator owns validation, archiving, and publishing.

import logging
from dataclasses import dataclass


@dataclass(frozen=True)
class ActiveMeta:
    data_until: object
    scraped_on: object
    s3_key: str


@dataclass(frozen=True)
class ScrapeResult:
    kind: str                 # "new_data" | "no_new_data"
    path: str | None = None
    data_until: object = None
    scraped_on: object = None
    reason: str | None = None

    @classmethod
    def new_data(cls, path, data_until, scraped_on):
        return cls("new_data", path=path, data_until=data_until, scraped_on=scraped_on)

    @classmethod
    def no_new_data(cls, reason):
        return cls("no_new_data", reason=reason)


def _start_driver(download_dir):
    from data_scraping.scraping_utilities.driver import start_driver
    return start_driver(headless=True, download_dir=download_dir)


class ScrapeContext:
    def __init__(self, work_dir, active, log):
        self.work_dir = work_dir
        self.active = active
        self.log = log or logging.getLogger("data_scraping")
        self._driver = None

    @property
    def driver(self):
        if self._driver is None:
            self._driver = _start_driver(self.work_dir)
        return self._driver

    def close(self):
        if self._driver is not None:
            try:
                self._driver.quit()
            finally:
                self._driver = None
