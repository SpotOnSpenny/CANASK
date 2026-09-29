# Python Standard Library Imports
import os
import shutil

# External Dependency Imports
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

#####################################################################################
#                                      Notes:                                       #
# This file contains the code that runs the selenium driver that is used for        #
# scraping data from the sources. The code in this file will essentially open a web #
# browser which will need to be passed through to the functions that do the actual  #
# data collection. The benefit of this structure is that the same driver can be     #
# used for multiple scrapes sequentially instead of needing to open a new browser   #
# instance, which will make things cleaner in the long run.                         #
#                                                                                   #
# Chromium + chromedriver come from the OS (Dockerfile installs Debian's            #
# chromium/chromium-driver), so one code path covers x86 and ARM. This eliminates   #
# the need to bundle per-architecture chromedriver binaries and ensures             #
# compatibility across platforms without manual platform detection.                 #
#                                                                                   #
# The scripts operate using a headless browser, which means the browser window will #
# not actually open on the screen. Do not be alarmed if you don't see anything      #
# happening! Chrome must be installed so that the headless version of the           #
# chromedriver can run. More details are available in the readme.                   #
#####################################################################################

def start_driver(headless=True, download_dir=None):
    # Chromium + chromedriver come from the OS (Dockerfile installs Debian's chromium/chromium-driver),
    # so one code path covers x86 and ARM. download_dir=True is the legacy "repo output/" behaviour for
    # the not-yet-refactored scrapers' __main__ blocks.
    chromedriver_path = shutil.which("chromedriver")
    if not chromedriver_path:
        raise RuntimeError("chromedriver not found on PATH (install chromium-driver)")
    options = Options()
    for binary in ("/usr/bin/chromium", "/usr/bin/chromium-browser"):
        if os.path.exists(binary):
            options.binary_location = binary
            break
    if headless:
        options.add_argument("--headless=new")
    # Container essentials: no user-namespace sandbox in Docker, and /dev/shm is tiny by default.
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    if download_dir is True:
        download_dir = os.path.join(os.getcwd(), "output")
    if download_dir:
        options.add_experimental_option("prefs", {"download.default_directory": download_dir})
    return webdriver.Chrome(service=Service(executable_path=chromedriver_path), options=options)


# Test code below
if __name__ == "__main__":
    start_driver()