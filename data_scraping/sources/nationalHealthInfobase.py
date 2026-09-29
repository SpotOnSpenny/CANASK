# Python Standard Library Imports
import datetime
import io
import os
import zipfile

# External Dependency Imports
import pandas
import urllib3

# Internal Dependency Imports
from data_scraping.context import ScrapeResult

#######################################################################################
#                                        Notes:                                       #
#######################################################################################
# Selenium is imported lazily inside _download_link() -- this module must stay importable
# on the web image, which has no Selenium installed (see requirements.webapp.txt).

PAGE_URL = "https://health-infobase.canada.ca/substance-related-harms/opioids-stimulants/"
KEY = "nationalHealthInfobase"

# The label's quarter -> the first month AFTER it (data runs "until" that date).
_QUARTER_NEXT_MONTH = {"Q1": 4, "Q2": 7, "Q3": 10, "Q4": 1}


def data_until_from_quarter(label):
    year, quarter = str(label).replace("\xa0", " ").split()[:2]
    month = _QUARTER_NEXT_MONTH[quarter]
    return datetime.date(int(year) + (1 if quarter == "Q4" else 0), month, 1)


def _download_link(driver):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions
    from selenium.webdriver.support.ui import WebDriverWait

    driver.get(PAGE_URL)
    return WebDriverWait(driver, 30).until(expected_conditions.presence_of_element_located(
        (By.XPATH, '//a[contains(@class, "dataDownload")]'))).get_attribute("href")


def _fetch(url):
    http = urllib3.PoolManager(retries=urllib3.Retry(total=1, backoff_factor=5))
    response = http.request("GET", url, timeout=urllib3.Timeout(connect=30, read=300))
    if response.status != 200:
        raise RuntimeError(f"download failed: HTTP {response.status}")
    return response.data


def scrape(ctx):
    link = _download_link(ctx.driver)
    archive = zipfile.ZipFile(io.BytesIO(_fetch(link)))
    member = next((m for m in archive.infolist() if m.filename.endswith(".csv")), None)
    if member is None:
        raise RuntimeError("no CSV inside the Health Infobase download")
    scraped_on = datetime.date(*member.date_time[:3])
    if ctx.active and ctx.active.scraped_on and scraped_on <= ctx.active.scraped_on:
        return ScrapeResult.no_new_data(f"download dated {scraped_on}, active version {ctx.active.scraped_on}")
    data = archive.read(member)
    frame = pandas.read_csv(io.BytesIO(data))
    last = frame[(frame["PRUID"] == 1) & (frame["Time_Period"] == "By quarter")
                 & (frame["Source"] == "Deaths")]["Year_Quarter"].iloc[-1]
    data_until = data_until_from_quarter(last)
    path = os.path.join(ctx.work_dir, f"{scraped_on:%Y%m%d}_{data_until:%Y%m%d}_{KEY}.csv")
    with open(path, "wb") as handle:
        handle.write(data)
    return ScrapeResult.new_data(path, data_until, scraped_on)


# Manual run: python -m data_scraping.sources.nationalHealthInfobase  (writes into ./output)
if __name__ == "__main__":
    from data_scraping.context import ScrapeContext
    ctx = ScrapeContext(os.path.join(os.getcwd(), "output"), None, None)
    try:
        print(scrape(ctx))
    finally:
        ctx.close()
