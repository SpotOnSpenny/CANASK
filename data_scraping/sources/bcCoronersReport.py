# Python Standard Library Imports
import sys
import datetime
import traceback
import time
import os

# External Dependency Imports
import pandas
import bs4
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, ElementClickInterceptedException
from selenium.webdriver import ActionChains
from alive_progress import alive_bar

# Internal Dependency Imports
sys.dont_write_bytecode = True
sys.path.append("data_scraping/scraping_utilities")
from checkUps import checkup_output
from driver import start_driver


#######################################################################################
#                                       Notes:                                        #
# PowerBI proved somewhat funky, but after dealing with it for a while, I was able to #
# write out some generalized functions to assist with parsing the data based on the   #
# format that it is presented in. These functions are in this file for now, but if we #
# come across any more PowerBI sources, we can very likely use them to scrape away    #
# whatever data that we choose.                                                       #
#                                                                                     #
# In general, the pattern is to find the table and load it as a BS4 soup for parsing, #
# which then gets converted into a dataframe. The reason for this is because PowerBI  #
# has a funny habit of re-loading elements when it doesn't need to which results in   #
# almost constant stale reference errors in selenium. By saving the HTML and parsing  #
# it with bs4 we can avoid this by saving the HTML to memory as soon as we obtain it. #
# There are also a number of options that can be passed to the function that does this#
# which relate to things like the title and format of the data presented. For instence# 
# the monthly data is presented in a way that requires a bit of extra work to get the #
# correct headers, so there's an option for that.                                     #
#                                                                                     #
# There are also some special cases where the data is initiatlly presented on the     #
# page as a visualization with no actual data present. This requires us to use the    #
# Selenium action chain to context click the graph, which then allows us to view the  #
# data as a chart which we can collect. This is all accomplished within the           #
# show_as_table function, which needs the graph element, driver and any additional    #
# title options which the parse_powerBI_table function needs for the table.           #
#                                                                                     #
# For maintainability, there are also comments above each block of code which detail  #
# what page the code is designed to scrape from. If there are any changes to the      #
# number of charts or tables compared to the expected number, the function will warn  #
# you so that you can check the source for any changes. In this case, the function    #
# will attempt to scrape but will likely fail as the rules are on the wrong pages.    #
# If this becomes a consistent problem needing to be fixed, we can always add a bit   #
# that checks for the title of the page and turn the sequential scraping into a match #
# case statement, but for now, we'll leave it as is because it does the job!          #
#######################################################################################

# Function to pull the data from the BC Coroners Report
def bc_coronersreport_scrape(driver, expected_pages:int = 18):
    # Instantiate any needed variables for the function
    dataframes = []
    existing_file = None

    # Load the Coroners Report page and get to data, also check the date of the report against existing scrapes to see if we need to run
    driver.get("https://app.powerbi.com/view?r=eyJrIjoiNjhiYjgxYzUtYjIyOC00ZGQ2LThhMzEtOWU5Y2Q4YWI0OTc5IiwidCI6IjZmZGI1MjAwLTNkMGQtNGE4YS1iMDM2LWQzNjg1ZTM1OWFkYyJ9")
    time.sleep(5) # Wait for page to finish loading and settle so that we don't get stale element errors
    date = WebDriverWait(driver, 15).until(expected_conditions.presence_of_element_located((By.XPATH, "//*[contains(text(), 'refreshed')]"))).text.split("refreshed ")[1].replace(" ", "").replace(".", "")
    date = datetime.datetime.strptime(date, "%d%b%Y").strftime("%Y%m%d")
    data_until = WebDriverWait(driver, 15).until(expected_conditions.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Data up to')]"))).text.split("of ")[1].replace(" ", "").replace(".", "")
    data_until = datetime.datetime.strptime(data_until, "%b%Y").strftime("%Y%m")
    print(f"Data refreshed on {date} with data up to {data_until}")
    quit(1) # This is a temporary quit to stop the script from running, remove this line when ready to scrape
    output_dir, needed_files, existing_files = checkup_output(["bcCoronersReport"])
    if existing_files == []:
        print("No existing files found for this source. Scraping data...")
    for file in existing_files:
        if "bcCoronersReport" in file and file.split("_")[0] == date:
            print("Data for this source is already up to date! Exiting...")
            quit(1)
        elif "bcCoronersReport" in file and file.split("_")[0] != date:
            print("Data for this source has been updated since last scrape! Scraping data...")
            existing_file = file
    pages = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//a[@class='middleText']"))).text.split("of")[1].strip()
    if int(pages) != expected_pages:
        print(f"Expected {expected_pages} pages, but found {pages} pages. Please check the source for any changes.")
        print("Proceeding with scrape, but source is likely to fail or have missing data")
    next_button = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//button[@aria-label='Next Page']")))

    # Start the scraping process
    with alive_bar((expected_pages - 2), title="Scraping BC Coroners Report") as bar: # -2 for the summary page at the beginning and notes page at the end
        # BC Page: There's only one table on this page so we can just find it and load it to a dataframe!
        next_button.click()
        grid_area = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]"))).get_attribute("outerHTML")
        table = bs4.BeautifulSoup(grid_area, "html.parser")
        dataframes.append(parse_powerBI_table(table))
        bar()

        # Age Group Page: there are two tables and a monthly/yearly toggle. Tables must be scraped and then toggled and scraped again
        next_button.click()
        grids = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]")))
        grids = [grid.get_attribute("outerHTML") for grid in grids]
        for grid in grids:
            table = bs4.BeautifulSoup(grid, "html.parser")
            dataframes.append(parse_powerBI_table(table))
        WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[contains(text(), 'Monthly')]/ancestor-or-self::visual-modern"))).click()
        grids = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]")))
        grids = [grid.get_attribute("outerHTML") for grid in grids]
        for grid in grids:
            table = bs4.BeautifulSoup(grid, "html.parser")
            dataframes.append(parse_powerBI_table(table, "monthly"))
        bar()

        # Sex Page: there are options for each health authority. These must be toggled and scraped
        next_button.click()
        options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
        for option in options:
            option.click()
            time.sleep(2) # Wait for the new table values to load
            grids = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]")))
            grids = [grid.get_attribute("outerHTML") for grid in grids]
            for grid in grids:
                table = bs4.BeautifulSoup(grid, "html.parser")
                dataframes.append(parse_powerBI_table(table, add_to_title=f"{option.text} Health Authority"))
        bar()

        # HA Year Page: Nothing special on the next page, just pull the tables
        next_button.click()
        grids = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]")))
        grids = [grid.get_attribute("outerHTML") for grid in grids]
        for grid in grids:
            table = bs4.BeautifulSoup(grid, "html.parser")
            dataframes.append(parse_powerBI_table(table))
        bar()

        # HA Monthly Page: Same for the next page, but it's montly data so need to specify that in the table parser
        next_button.click()
        grids = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]")))
        grids = [grid.get_attribute("outerHTML") for grid in grids]
        for grid in grids:
            table = bs4.BeautifulSoup(grid, "html.parser")
            dataframes.append(parse_powerBI_table(table, extra_header="monthly"))
        bar()

        # HSDA page: Need to toggle yearly/monthly to pull all data from the tables
        next_button.click()
        grids = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]")))
        grids = [grid.get_attribute("outerHTML") for grid in grids]
        for grid in grids:
            table = bs4.BeautifulSoup(grid, "html.parser")
            dataframes.append(parse_powerBI_table(table))
        WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[contains(text(), 'Monthly')]/ancestor-or-self::visual-modern"))).click()
        grids = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]")))
        grids = [grid.get_attribute("outerHTML") for grid in grids]
        for grid in grids:
            table = bs4.BeautifulSoup(grid, "html.parser")
            dataframes.append(parse_powerBI_table(table, "monthly"))
        bar()

        # LHA Page: No toggles needed, just pull from the page
        next_button.click()
        grids = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]")))
        grids = [grid.get_attribute("outerHTML") for grid in grids]
        for grid in grids:
            table = bs4.BeautifulSoup(grid, "html.parser")
            dataframes.append(parse_powerBI_table(table, special_rows=True))
        bar()

        # Township of Injury Page: No toggles, but one table has a monthly header
        next_button.click()
        grids = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]")))
        grids = [grid.get_attribute("outerHTML") for grid in grids]
        for grid in grids:
            table = bs4.BeautifulSoup(grid, "html.parser")
            try:
                dataframes.append(parse_powerBI_table(table))
            except:
                dataframes.append(parse_powerBI_table(table, "monthly"))
        bar()

        # Place of Injury Page: No toggles, but there's a Number/Percent for each year
        next_button.click()
        grid_area = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]"))).get_attribute("outerHTML")
        table = bs4.BeautifulSoup(grid_area, "html.parser")
        dataframes.append(parse_powerBI_table(table, "yearly"))
        bar()

        # Drugs Involved Page: The table first needs to be pulled, and then Health Authority must be toggled, but Skip the "Select All" option
        next_button.click()
        grid_area = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]"))).get_attribute("outerHTML")
        table = bs4.BeautifulSoup(grid_area, "html.parser")
        dataframes.append(parse_powerBI_table(table))
        options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
        for option in options:
            if option.text.lower() != "select all":
                option.click()
                time.sleep(2)
                grids = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]")))
                grids = [grid.get_attribute("outerHTML") for grid in grids]
                for grid in grids:
                    table = bs4.BeautifulSoup(grid, "html.parser")
                    dataframes.append(parse_powerBI_table(table, add_to_title=f"{option.text} Health Authority"))
        bar()

        # Fentanyl Detected Page: This has a graph, need to ask PowerBI to show as a table, then scrape the table, and go back to the graph to click relevant options
        next_button.click()
        graph = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[contains(@class, 'hundredPercentStackedColumnChart')]")))
        show_as_table(dataframes, driver, graph, add_to_title="All Health Authorities")
        options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
        len_options = len(options)
        clicked = ["select all"]
        while len(clicked) != len_options:
            try:
                for option in options:
                    if option.text.lower() not in clicked:
                        clicked.append(option.text.lower())
                        option.click()
                        time.sleep(2)
                        graph = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[contains(@class, 'hundredPercentStackedColumnChart')]")))
                        show_as_table(dataframes, driver, graph, add_to_title=f"{option.text} Health Authority")
            except StaleElementReferenceException:
                options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
        # Repeat this with the carfentanil option
        WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Carfentanil')]/ancestor-or-self::visual-modern"))).click()
        graph = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[contains(@class, ' visual-columnChart')]")))
        show_as_table(dataframes, driver, graph, add_to_title="All Health Authorities")
        options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
        len_options = len(options)
        clicked = ["select all"]
        while len(clicked) != len_options:
            try:
                for option in options:
                    if option.text.lower() not in clicked:
                        clicked.append(option.text.lower())
                        option.click()
                        time.sleep(2)
                        graph = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[contains(@class, ' visual-columnChart')]")))
                        show_as_table(dataframes, driver, graph, add_to_title=f"{option.text} Health Authority")
            except StaleElementReferenceException:
                options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
        bar()

        # Fentanyl Concentration Page: A Graph, like previously, but without the Carfentanil option, so once over will do nicely
        next_button.click()
        graph = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[contains(@class, 'hundredPercentStackedColumnChart')]")))
        show_as_table(dataframes, driver, graph, add_to_title="All Health Authorities")
        options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
        len_options = len(options)
        clicked = ["select all"]
        while len(clicked) != len_options:
            try:
                for option in options:
                    if option.text.lower() not in clicked:
                        clicked.append(option.text.lower())
                        option.click()
                        time.sleep(2)
                        graph = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[contains(@class, 'hundredPercentStackedColumnChart')]")))
                        show_as_table(dataframes, driver, graph, add_to_title=f"{option.text} Health Authority")
            except StaleElementReferenceException:
                options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
        bar()

        #Expedited Tox 1 Page: Just need to pull the data from the chart that's beside the graph on the page
        next_button.click()
        grid = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]"))).get_attribute("outerHTML")
        table = bs4.BeautifulSoup(grid, "html.parser")
        dataframes.append(parse_powerBI_table(table))
        options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
        len_options = len(options)
        clicked = ["select all"]
        while len(clicked) != len_options:
            try:
                for option in options:
                    if option.text.lower() not in clicked:
                        option.click()
                        clicked.append(option.text.lower())
                        time.sleep(2)
                        grid = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]"))).get_attribute("outerHTML")
                        table = bs4.BeautifulSoup(grid, "html.parser")
                        dataframes.append(parse_powerBI_table(table, add_to_title=f"{option.text} Health Authority"))
            except StaleElementReferenceException:
                options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
        bar()

        # Expedited Tox 2 Page: The page is a graph with two sets of options. For each year and each health authority, we need to pull the data by requesting the chart
        next_button.click()
        graph = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//*[@class='cartesianChart']")))
        show_as_table(dataframes, driver, graph, add_to_title="All Health Authorities")
        options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
        len_options = len(options)
        clicked = ["select all"]
        while len(clicked) != len_options:
            try:
                for option in options:
                    if option.text.lower() not in clicked:
                        clicked.append(option.text.lower())
                        option.click()
                        time.sleep(2)
                        graph = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[contains(@class, ' visual-columnChart')]")))
                        show_as_table(dataframes, driver, graph, add_to_title=f"{option.text} Health Authority")
                    if len(clicked) == len_options: # If it's the last option, click it again to turn it off and get the "overall" for the next year
                        option.click()
            except StaleElementReferenceException:
                options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
        # Repeat this with each of the year options, a nested loop is used here to iterate over each of the options for eahc of the years
        years = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerCheckbox')]/..")))
        len_years = len(years)
        years_collected = []
        while len(years_collected) != len_years:
            try:
                for year in years:
                    year_text = year.text
                    if year_text not in years_collected:
                        years_collected.append(year_text)
                        year.click()
                        time.sleep(2)
                        options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
                        len_options = len(options)
                        clicked = []
                        while len(clicked) != len_options:
                            try:
                                for option in options:
                                    if option.text.lower() not in clicked:
                                        title_change_flag = False
                                        clicked.append(option.text.lower())
                                        option.click()
                                        time.sleep(2)
                                        if option.text.lower() == "select all":
                                            option.click()
                                            time.sleep(2)
                                            title_change_flag = True
                                        graph = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[contains(@class, ' visual-columnChart')]")))
                                        show_as_table(dataframes, driver, graph, add_to_title=(f"{year_text} {option.text} Health Authority" if not title_change_flag else f"{year_text} All Health Authorities"))
                            except StaleElementReferenceException:
                                options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
            except StaleElementReferenceException or ElementClickInterceptedException:
                print("An exception occured clicking a year! Refreshing the years list and trying again...")
                time.sleep(2)
                years = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerCheckbox')]/..")))
        bar()

        #Mode of Consumption Page: It's just a graph with the single set of options, can re-use code already written
        next_button.click()
        graph = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//*[contains(@class, 'cartesianChart')]")))
        show_as_table(dataframes, driver, graph, add_to_title="All Health Authorities")
        options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
        len_options = len(options)
        clicked = ["select all"]
        while len(clicked) != len_options:
            try:
                for option in options:
                    if option.text.lower() not in clicked:
                        clicked.append(option.text.lower())
                        option.click()
                        time.sleep(2)
                        graph = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//*[@class='cartesianChart']")))
                        show_as_table(dataframes, driver, graph, add_to_title=f"{option.text} Health Authority")
            except StaleElementReferenceException:
                options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
        bar()

        # Income Assistance Day Page: Just a single graph on this page, so we can just find the chart and pass it into the show as table function
        next_button.click()
        graph = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//*[@class='cartesianChart']")))
        show_as_table(dataframes, driver, graph)
        bar()

    # Output all the dataframes to an excel spreadsheet with different worksheets for each dataframe
    with pandas.ExcelWriter(os.path.join(output_dir, f"{date}_{data_until}_bcCoronersReport.xlsx")) as writer:
        for index, dataframe in enumerate(dataframes):
            dataframe.to_excel(writer, sheet_name=f"Table {index + 1}")
    print("Data successfully scraped and saved!")

    # Cleanup any existing old files
    if existing_file:
        os.remove(os.path.join(output_dir, existing_file))

# Function that clicks the provided graph and returns the table generated by PowerBI as a dataframe
def show_as_table(dataframes, driver, graph, add_to_title = None):
    ActionChains(driver).context_click(graph).perform()
    WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//button[@aria-label='Show as a table']"))).click()
    grids = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'interactive-grid')]")))
    grids = [grid.get_attribute("outerHTML") for grid in grids]
    title = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[@class='popOutBar']/div[contains(@class, 'title')]"))).text
    for grid in grids:
        table = bs4.BeautifulSoup(grid, "html.parser")
        dataframes.append(parse_powerBI_table(table, full_title=title, add_to_title=add_to_title))
    WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//button[@aria-label='Back to report']"))).click()

# Function that enables parsing of PowerBI tables into dataframes
def parse_powerBI_table(table, extra_header=None, special_rows = False, full_title = None, add_to_title = None):
    if full_title:
        table_title = full_title
    else:
        table_title = table.find("div", {"class": "visualTitleArea"}).text
    if add_to_title:
        table_title = f"{add_to_title}: {table_title}"
    rows = table.find_all("div", {"role": "row"})
    if not extra_header:
        header = rows.pop(0).find_all("div", {"role": "columnheader"})
        header = [column.text for column in header]
    elif extra_header.lower() == "monthly":
        rows.pop(0)
        columns = rows.pop(0)
        columns = [cell.text.replace(u"\xa0", "") for cell in columns.find_all("div", {"role": "columnheader"})]
        current_year = datetime.datetime.now().year
        columns.reverse()
        header = []
        for index, column in enumerate(columns):
            if column == "Dec":
                current_year -= 1
            if index != len(columns) - 1:
                column = f"{current_year} {column}"
            header.append(column)
        header.reverse()
    elif extra_header.lower() == "yearly":
        rows.pop(0)
        columns = rows.pop(0)
        columns = [cell.text.replace(u"\xa0", "") for cell in columns.find_all("div", {"role": "columnheader"})]
        current_year = datetime.datetime.now().year
        columns.reverse()
        repeat = []
        header = []
        repeat_set = False
        for index, column in enumerate(columns):
            if len(repeat) == 0:
                repeat.append(column)
            elif column != repeat[0] and repeat_set == False:
                repeat.append(column)
            elif column == repeat[0] and repeat_set == False:
                repeat_set = True
                current_year -= 1
            elif column == repeat[0] and repeat_set == True:
                current_year -= 1             
            if index != len(columns) - 1:
                column = f"{current_year} {column}"
            header.append(column)
        header.reverse()
    table_dataframe = pandas.DataFrame(columns=header)
    for row in rows:
        if not special_rows:
            row_header = row.find("div", {"role": "rowheader"}).text
        else:
            row_header = None
        row_data = [cell.text for cell in row.find_all("div", {"role": "gridcell"})]
        if row_header:
            row_data = [row_header, *row_data]
        table_dataframe.loc[len(table_dataframe)] = row_data
    table_dataframe.columns = pandas.MultiIndex.from_product([[table_title], table_dataframe.columns])
    return table_dataframe

# Test code below
if __name__ == "__main__":
    try:
        driver = start_driver(headless=False)
        bc_coronersreport_scrape(driver, 18)
        driver.quit()
    except Exception as e:
        traceback.print_exc()
        driver.quit()