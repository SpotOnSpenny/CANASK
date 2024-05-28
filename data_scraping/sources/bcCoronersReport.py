# Python Standard Library Imports
import sys
import datetime
import traceback
import time

# External Dependency Imports
import pandas
import bs4
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
from selenium.webdriver import ActionChains

# Internal Dependency Imports
sys.dont_write_bytecode = True
sys.path.append("data_scraping/scraping_utilities")
from checkUps import checkup_output
from driver import start_driver


#######################################################################################
#                                       Notes:                                        #
#######################################################################################

# Function to pull the data from the BC Coroners Report
def bc_coronersreport_scrape(driver):
    # Instantiate any needed variables for the function
    dataframes = []

    # Load the Coroners Report page and get to data
    driver.get("https://app.powerbi.com/view?r=eyJrIjoiY2NhOWZhNzMtZTFlNC00NTI2LTkwNTgtNzdmYjNjMTViMTQzIiwidCI6IjZmZGI1MjAwLTNkMGQtNGE4YS1iMDM2LWQzNjg1ZTM1OWFkYyJ9")
    time.sleep(5) # Wait for page to settle before finding elements, otherwise they return stale
    date = WebDriverWait(driver, 15).until(expected_conditions.presence_of_element_located((By.XPATH, "//*[contains(text(), 'refreshed')]"))).text.split("refreshed ")[1].replace(" ", "").replace(".", "")
    date = datetime.datetime.strptime(date, "%d%b%Y").strftime("%Y%m%d")
    next_button = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//button[@aria-label='Next Page']")))
    next_button.click()

    # BC Page: Use selenium to find the table, then use bs4 to parse the table because PowerBI likes to reload elements
    grid_area = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]"))).get_attribute("outerHTML")
    table = bs4.BeautifulSoup(grid_area, "html.parser")
    dataframes.append(parse_powerBI_table(table))

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

    # Sex Page: there are options for each health authority. These must be toggled and scraped
    next_button.click()
    options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
    for option in options:
        option.click()
        grids = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]")))
        grids = [grid.get_attribute("outerHTML") for grid in grids]
        for grid in grids:
            table = bs4.BeautifulSoup(grid, "html.parser")
            dataframes.append(parse_powerBI_table(table, add_to_title=f"{option.text} Health Authority"))

    # HA Year Page: Nothing special on the next page, just pull the tables
    next_button.click()
    grids = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]")))
    grids = [grid.get_attribute("outerHTML") for grid in grids]
    for grid in grids:
        table = bs4.BeautifulSoup(grid, "html.parser")
        dataframes.append(parse_powerBI_table(table))

    # HA Monthly Page: Same for the next page, but it's montly data so need to specify that in the table parser
    next_button.click()
    grids = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]")))
    grids = [grid.get_attribute("outerHTML") for grid in grids]
    for grid in grids:
        table = bs4.BeautifulSoup(grid, "html.parser")
        dataframes.append(parse_powerBI_table(table, extra_header="monthly"))

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

    # LHA Page: No toggles needed, just pull from the page
    next_button.click()
    grids = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]")))
    grids = [grid.get_attribute("outerHTML") for grid in grids]
    for grid in grids:
        table = bs4.BeautifulSoup(grid, "html.parser")
        dataframes.append(parse_powerBI_table(table, special_rows=True))

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

    # Place of Injury Page: No toggles, but there's a Number/Percent for each year
    next_button.click()
    grid_area = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]"))).get_attribute("outerHTML")
    table = bs4.BeautifulSoup(grid_area, "html.parser")
    dataframes.append(parse_powerBI_table(table, "yearly"))
    
    # Drugs Involved Page: The table first needs to be pulled, and then Health Authority must be toggled, but Skip the "Select All" option
    next_button.click()
    grid_area = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]"))).get_attribute("outerHTML")
    table = bs4.BeautifulSoup(grid_area, "html.parser")
    dataframes.append(parse_powerBI_table(table))
    options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
    for option in options:
        if option.text.lower() != "select all":
            option.click()
            grids = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]")))
            grids = [grid.get_attribute("outerHTML") for grid in grids]
            for grid in grids:
                table = bs4.BeautifulSoup(grid, "html.parser")
                dataframes.append(parse_powerBI_table(table, add_to_title=f"{option.text} Health Authority"))

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
                    graph = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[contains(@class, ' visual-columnChart')]")))
                    show_as_table(dataframes, driver, graph, add_to_title=f"{option.text} Health Authority")
        except StaleElementReferenceException:
            options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))

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
                    graph = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[contains(@class, 'hundredPercentStackedColumnChart')]")))
                    show_as_table(dataframes, driver, graph, add_to_title=f"{option.text} Health Authority")
        except StaleElementReferenceException:
            options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))

    #Expedited Tox 1 Page: Just need to pull the data from the chart that's beside the graph on the page
    next_button.click()
    options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
    for option in options:
        option.click()
        grids = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[@role='grid' and contains(@class, 'interactive-grid')]/ancestor-or-self::div[contains(@class, 'visualWrapper')]")))
        grids = [grid.get_attribute("outerHTML") for grid in grids]
        for grid in grids:
            table = bs4.BeautifulSoup(grid, "html.parser")
            dataframes.append(parse_powerBI_table(table, add_to_title=f"{option.text} Health Authority"))

    # Expedited Tox 2 Page: The page is a graph with two sets of options. For each year and each health authority, we need to pull the data by requesting the chart
    next_button.click()
    graph = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//svg[@class='cartesianChart']")))
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
                    graph = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//div[contains(@class, ' visual-columnChart')]")))
                    show_as_table(dataframes, driver, graph, add_to_title=f"{option.text} Health Authority")
        except StaleElementReferenceException:
            options = WebDriverWait(driver, 10).until(expected_conditions.presence_of_all_elements_located((By.XPATH, "//div[contains(@class, 'slicerItemsContainer')]/div/div[@role='option']")))
    # Repeat this with each of the year options
    # XPATH for the year check boxes is //div[contains(@class, 'slicerCheckbox')]
    # Parent of this will give you a span for the row, which text will give the year, and should also be clickable


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
        bc_coronersreport_scrape(driver)
        driver.quit()
    except Exception as e:
        traceback.print_exc()
        driver.quit()