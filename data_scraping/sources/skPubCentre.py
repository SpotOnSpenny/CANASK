# Python Standard Library Imports
import sys
import os
import shutil
import pandas
import datetime

# External Dependency Imports
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException
import urllib3
from pypdf import PdfReader
from alive_progress import alive_bar

# Internal Dependency Imports
sys.dont_write_bytecode = True
sys.path.append("data_scraping/scraping_utilities")
from driver import start_driver
from checkUps import checkup_output

#######################################################################################
#                                       Notes:                                        #
# This data source is quirky, and not like the other data sources. Instead of having  #
# data exist in a table, or a chart, or extractable format, it instead is a report in #
# PDF format. We can simply request the URL and download the PDF in order to get the  #
# raw data, but we'll still need to parse the PDF in order to extrac the data into a  #
# machine readable format like a CSV, as we have been for the other databases. PyPDF  #
# seems to be the best library for this task, so this file will use that.             #
#                                                                                     #
# The data source page is https://publications.saskatchewan.ca/#/products/90505, and  #
# there contains a link to the PDF that will allow us to download the report. Just to #
# be sure that we're getting the most up to date report, the script also will go to   #
# the data source page, and click the download link, in case the URL changes when the #
# report gets updated year over year.                                                 #
#                                                                                     #
# Parsing the PDF will be somewhat interesting as well as the PDF is essentially just #
# a bunch of tables with a hard to read structure. To do so, the script makes use of  #
# a pretty large match case statement which parses the doccument line by line and     #
# searches for the titles of tables that we expect to see in the report. This means   #
# that the script will always be able to find the tables, and won't have an issue as  #
# new years/months are updated and added. However, the tradeoff here is that the      #
# script will need to be updated with new titles and new ruels when new tables are    #
# added. In order to watch for this, the script will first check how many pages are   #
# in the report, to see if it matches what we expect to see. If there are more tables #
# then there will be more pages, and the script will spit out a warning stating this. #
# The script will also not delete the raw pdf so that there's an opportunity to check #
# and see what data was missed if any, or otherwise update the scripts expected pages #
#######################################################################################

# Function to scrape the Saskatchewan Publication Centre coroners report
def sk_pubcentre_scrape(driver, expected_pages):
    # Check for files and output directory
    output_dir, needed_files, existing_files = checkup_output(["skPubCentre"])
    # Check if there's existing data
    date_scraped = None
    for file in existing_files:
        if "skPubCentre" in file:
            date_scraped = file.split("_")[0]
            existing_file = file
            break
    # Instantiate the http pool for requests
    http = urllib3.PoolManager()
    # Navigate to the data source page
    driver.get("https://publications.saskatchewan.ca/#/products/90505")
    # Find the download link for the report
    try:
        download_link = WebDriverWait(driver, 10).until(expected_conditions.presence_of_element_located((By.XPATH, "//li[@class='ng-scope']/a"))).get_attribute("href")
    except TimeoutException:
        print("The download link was not found on the page.")
        return
    # Download the report with the http pool
    raw_data_file = os.path.join(output_dir, "raw_pdf.pdf")
    with http.request('GET', download_link, preload_content=False) as data_response, open(raw_data_file, 'wb') as out_file:       
        print("Downloading the PDF report...")
        shutil.copyfileobj(data_response, out_file)
    # Release the connection stream
    data_response.release_conn()
    # Get the date that the document was published from the PDF metadata then remove it from memory
    reader = PdfReader(raw_data_file)
    date_of_report = str(reader.metadata.creation_date).split(" ")[0].replace("-", "")
    pages = len(reader.pages)
    # Check if the current data is already up to date
    if date_of_report == date_scraped:
        print("The data is already up to date.")
        return
    else:
        print("A newer report has been published! Extracting data from the newest report...")
    # If we aren't up to date,check to see if anything new and spicy has been added to the report by checking the number of pages
    dont_delete = False
    if pages != expected_pages:
        print("The number of pages in the report has changed but the scraper will only catch expected tables.")
        print("Running the extraction anyways, but be sure to check the raw data for missing data!")
        print("The raw data will not be deleted from the output directory for follow up use.")
        dont_delete = True
    # Look for the table titles in the pages of the report
    titles =[
        "Confirmed Drug Toxicity Deaths by Manner of Death",
        "Suspected Drug Toxicity Deaths",
        "Breakdown of Opioid Drugs Identified in Confirmed Drug Toxicity Deaths by Manner of Death",
        "Breakdown of Benzodiazepine Drugs Identified in Confirmed Drug Toxicity Deaths by Manner of Death",
        "Confirmed Drug Toxicity Deaths Involving Opioid Drugs by Manner of Death, Sex and Race",
        "Confirmed Drug Toxicity Deaths Involving Opioid Drugs by Manner of Death, Sex and Age Group",
        "Confirmed Drug Toxicity Deaths Involving Fentanyl by Manner of Death, Sex and Race",
        "Confirmed Drug Toxicity Deaths Involving Fentanyl by Manner of Death, Sex and Age Group",
        "Benzodiazepines by Manner of Death, Sex and",
        "Confirmed Drug Toxicity Deaths by Place of Death",
        "Number of Confirmed Deaths Where Methamphetamine Toxicity was Part of the Cause of Death",
        "Xylazine Toxicity",
    ]
    # Instantiate a list of data frames to hold the tables
    report_tables = []
    # Put each new line of the PDF into a list
    pages = [page.extract_text() for page in reader.pages]
    pdf_lines = []
    for page in pages:
        pdf_lines.extend(page.split("\n"))
    # Instantiate some variables to help with iteration
    searching = True
    join_with_next = False
    header = ""
    spaceless_titles = [title.lower().replace(" ", "") for title in titles]
    end_of_table = ["", " ", "  ", "   ", "/n"]
    table_footnotes = ["Please note", "This current table includes"]
    non_title_indicators = ["updated", "statistics", "shown"]
    title = ""
    previous_title = ""
    title_set = False
    ignore_next = False
    skip_until = 0
    save_table = False
    # Iterate over the lines of the PDF until we find a title
    # Using spaceless titles and lines to account for weird spacing from PyPDF
    for line_index, line in enumerate(pdf_lines):
        spaceless_line = line.lower().replace(" ", "")
        skip_line = False
        # If we find a title, start a new dataframe, also check to ensure the title is not just in a line of other text
        if any(title in spaceless_line for title in spaceless_titles) and searching and not any(indicator in spaceless_line for indicator in non_title_indicators):
            table = pandas.DataFrame({})
            searching = False
            spaceless_title = spaceless_line
            title = line
        elif not searching and line not in end_of_table and not any(footnote in line for footnote in table_footnotes):
            # Add the line to the dataframe until we find the end of the table
            # Check the case for the current title as well and apply the given rules to the data
            match title:
                case title if spaceless_titles[0] in spaceless_title and "breakdown" not in spaceless_title:
                    # Strip out leading/trailg whitespace and doublespaces
                    line = line.strip().replace("  ", " ")
                    # If the first character is a number, it's the year row so insert a label
                    if line[0].isnumeric():
                        line = "Year " + line
                case title if spaceless_titles[1] in spaceless_title:
                    # Set the title to something more general
                    if title != titles[1]:
                        title = titles[1]
                    # Strip out leading/trailg whitespace and doublespaces
                    line = line.strip()
                    while "  " in line:
                        line = line.replace("  ", " ")
                    # If the line doesn't start with "Total", it's not a data row
                    if not line.startswith("Total") and not table.empty:
                        # Remove spaces and take the last 4 characters as the year for the next line
                        year = line.replace(" ", "")[-4:]
                        skip_line = True
                    # If the line starts with "Total", it's a data row so we should replace "total" with the year
                    elif line.startswith("Total") and not table.empty:
                        line = line.replace("Total", year).replace("*", "")
                    # If the line is a data row but the table is empty, it's the first line and we need to add the year from the title
                    elif line.startswith("Total") and table.empty:
                        line = line.replace("Total", spaceless_title[-4:]).replace("*", "")
                # Table 3 and 4 are the same format, so we can double up on the case
                case title if spaceless_titles[2] in spaceless_title or spaceless_titles[3] in spaceless_title:
                    # Strip out leading/trailg whitespace and doublespaces
                    line = line.strip().replace("  ", " ")
                    # Consolidate all the drug lines into one line
                    if not any(manner in line for manner in ["Accident", "Suicide", "Homicide", "Undetermined"]):
                        header = header + line
                        skip_line = True
                    # If it's the first row, add the header, and then the row as is
                    elif any(manner in line for manner in ["Accident", "Suicide", "Homicide", "Undetermined"]) and table.empty:
                        header = "Year MannerOfDeath " + header.replace(" -", "")
                        table = table._append(pandas.Series(header.split(" ")), ignore_index=True)
                        header = ""
                        year = line[:4]                        
                    # If it's not the first data row and a year is there, set the year as that
                    elif any(manner in line for manner in ["Accident", "Suicide", "Homicide", "Undetermined"]) and not table.empty and line[0].isnumeric():
                        year = line[:4]
                    # If it's not the first data row and the year is not there, set the year as the most recent year
                    elif any(manner in line for manner in ["Accident", "Suicide", "Homicide", "Undetermined"]) and not table.empty and not line[0].isnumeric():
                        line = f"{year} {line}"
                case title if spaceless_titles[4] in spaceless_title or spaceless_titles[6] in spaceless_title:
                    # Basic Cleaning  
                    line = line.strip().replace("non -s", "nonS")
                    while "  " in line: 
                        line = line.replace("  ", " ")
                    # If the line is a header
                    if line[0].isnumeric():
                        line = "MannerOfDeath Sex Race " + line
                    # If the line states the manner of death, it's the first line on the chart
                    elif any(manner in line for manner in ["Accident", "Suicide", "Homicide", "Undetermined"]):
                        manner = line.split(" ")[0]
                        sex = line.split(" ")[1]
                        skip_line = True
                    # If the line starts with the sex, then it's another header row and the sex needs to be updated
                    elif line.startswith("Male") or line.startswith("Female"):
                        sex = line
                        skip_line = True
                    # If there's a format error and the line ends with an &, set a flag to add the next line to it
                    elif line.endswith("&"):
                        join_with_next = True
                        previous = line
                        skip_line = True
                    # Otherwise, it's a data line, so we need to concatinate the non-numeric characters to be the "race"
                    else:
                        start = ""
                        # If there was a formating error last line, join the two lines together before looking through it
                        if join_with_next:
                            line = f"{previous} {line}"
                            line = line.replace("non-s", "nonS")
                            join_with_next = False
                        for index, character in enumerate(line):
                            if character.isnumeric() or character == "-":
                                line = f"{manner} {sex} {start.replace(' ', '')} {line[index:]}"
                                break
                            else:
                                start = f"{start}{character}"
                case title if spaceless_titles[5] in spaceless_title or spaceless_titles[7] in spaceless_title:
                    # Basic cleaning
                    line = line.strip()
                    while "  " in line:
                        line = line.replace("  ", " ")
                    # If the line is leftover from the title, skip it
                    if line.startswith("2016 -") and not join_with_next:
                        skip_line = True
                    # If the line is the years row, add some labels
                    elif line[:4].startswith("2016")and not join_with_next:
                        line = "MannerOfDeath Sex AgeGroup " + line
                    # Otherwise, it's a line of data! And the age group will need to be pushed together and year/manner columns added
                    elif line.startswith("Male") or line.startswith("Female")and not join_with_next:
                        sex = line.split(" ")[0]
                        line = f"{manner} {line.replace(' – ', '-').replace(' +', '+')}"
                    elif line.startswith("Total")and not join_with_next:
                        line = f"{manner} Male&Female {line.replace(' – ', '-').replace(' +', '+')}"                        
                    elif not line[0].isnumeric()and not join_with_next:
                        manner = line.split(" ")[0]
                        sex = line.split(" ")[1]
                        line = line.replace(" – ", "-").replace(" +", "+")
                    # If the line is very short, there was very likely a formatting error and we need to join it with the next line
                    elif len(line) <= 9 and not join_with_next:
                        join_with_next = True
                        previous = line
                        skip_line = True
                    elif join_with_next:
                        line = f"{manner} {sex} {previous}{line}"
                        line = line.replace(' – ', '-')
                        join_with_next = False                        
                    else:
                        line = f"{manner} {sex} {line.replace(' – ', '-').replace(' +', '+').replace(' - ', '-')}"
                case title if spaceless_titles[8] in spaceless_title and "age" not in spaceless_title:
                    if title_set == False:
                        title = "Confirmed Drug Toxicity Deaths Involving Benzodiazepines by Manner of Death, Sed and Race"
                        title_set = True
                    # Basic cleaning
                    while "  " in line:
                        line = line.replace("  ", " ")
                    line = line.strip().replace("non -s", "nonS")
                    # If leftover from title, skip!
                    if line.startswith("Race"):
                        skip_line = True
                        ignore_next = True
                    if line.startswith("2024") and not table.empty:
                        skip_line = True
                    # If the line is a header
                    if line[0].isnumeric():
                        line = "MannerOfDeath Sex Race " + line
                    # If the line states the manner of death, it's the first line on the chart
                    elif any(manner in line for manner in ["Accident", "Suicide", "Homicide", "Undetermined"]):
                        manner = line.split(" ")[0]
                        sex = line.split(" ")[1]
                        skip_line = True
                    # If the line starts with the sex, then it's another header row and the sex needs to be updated
                    elif line.startswith("Male") or line.startswith("Female"):
                        sex = line
                        skip_line = True
                    # If there's a format error and the line ends with an &, set a flag to add the next line to it
                    elif line.endswith("&"):
                        join_with_next = True
                        previous = line
                        skip_line = True
                    # Otherwise, it's a data line, so we need to concatinate the non-numeric characters to be the "race"
                    else:
                        start = ""
                        # If there was a formating error last line, join the two lines together before looking through it
                        if join_with_next:
                            line = f"{previous} {line}"
                            line = line.replace("non-s", "nonS")
                            join_with_next = False
                        for index, character in enumerate(line):
                            if character.isnumeric() or character == "-":
                                line = f"{manner} {sex} {start.replace(' ', '')} {line[index:]}"
                                break
                            else:
                                start = f"{start}{character}"
                case title if spaceless_titles[8] and "age" in spaceless_title:
                    # Basic cleaning
                    line = line.strip()
                    while "  " in line:
                        line = line.replace("  ", " ")
                    # If the line is leftover from the title, skip it
                    if line.startswith("Group"):
                        skip_line = True
                    # If the line is the years row, add some labels
                    elif line.startswith("2024"):
                        line = f"Manner Sex AgeGroup {line}" 
                    elif line.startswith("Male") or line.startswith("Female"):
                        sex = line.split(" ")[0]
                        line = f"{manner} {line.replace(' – ', '-').replace(' +', '+')}"
                    elif line.startswith("Total"):
                        line = f"{manner} Male&Female {line.replace(' – ', '-').replace(' +', '+')}"                        
                    elif not line[0].isnumeric():
                        manner = line.split(" ")[0]
                        sex = line.split(" ")[1]
                        line = line.replace(" – ", "-").replace(" +", "+")
                    else:
                        line = f"{manner} {sex} {line.replace(' – ', '-').replace(' +', '+').replace(' - ', '-')}"
                case title if spaceless_titles[9] in spaceless_title and "breakdown" not in spaceless_title:
                    # Basic cleaning
                    line = line.strip()
                    while "  " in line:
                        line = line.replace("  ", " ")
                    # If the line is a header
                    if line[0].isnumeric():
                        line = "Location " + line
                    # If the line is a data row, loop over the start of the line until we aren't looking at the location anymore
                    elif not line[0].isnumeric():
                        start = ""
                        for index, character in enumerate(line):
                            if character.isnumeric() or character == "-":
                                line = f"{start.replace(' ', '')} {line[index:]}"
                                break
                            else:
                                start = f"{start}{character}"
                case title if spaceless_titles[9] and "breakdown" in spaceless_title:
                    # Basic cleaning
                    line = line.strip()
                    while "  " in line:
                        line = line.replace("  ", " ")
                    # If the line is a header, skip it because we'll need to make the header manually
                    if line[0].isnumeric():
                        skip_line = True
                    # If the line is the one containing the drugs then use this to create the header with years
                    if line.startswith("F F C"):
                        years = "Year"
                        year_of_data = 2015
                        for drug in line.split(" "):
                            if drug == "F":
                                year_of_data += 1
                            years = f"{years} {year_of_data}"
                        # Add the header of years to the table
                        table = table._append(pandas.Series(years.split(" ")), ignore_index=True)
                        # Add a lable for the row of drugs
                        line = f"Drug {line}"
                    # If the line is a data row, loop over the start of the line until we aren't looking at the location anymore
                    elif not line[0].isnumeric():
                        start = ""
                        for index, character in enumerate(line):
                            if character.isnumeric() or character == "-":
                                line = f"{start.replace(' ', '')} {line[index:]}"
                                break
                            else:
                                start = f"{start}{character}"
                case title if spaceless_titles[10] in spaceless_title:
                    # Basic cleaning
                    line = line.strip()
                    while "  " in line:
                        line = line.replace("  ", " ")
                    # If the line is leftover from the title then skip it and ignore the next empty line
                    if line.startswith("2016 –") and table.empty:
                        skip_line = True
                        # Manually set the header
                        header = "Year DeathsWhereMethamphetamineToxicityWasSoleCause DeathsWhereCombinedToxicityWithMethamphetamineWasCause Total"
                        # Add the header to the table
                        table = table._append(pandas.Series(header.split(" ")), ignore_index=True)
                        # Skip to content
                        for skip_index, line in enumerate(pdf_lines[line_index + 1:]):
                            if line.startswith("2016"):
                                skip_until =  skip_index + line_index
                                break
                    # If the line is a row of non-data silly header formatting, just skip it
                    elif not line[0].isnumeric() and len(table) == 1:
                        skip_line = True
                    # If the row date is the current year, it's the end of the table, and we need to insert a table end indicator line in the lines list
                    elif line.startswith(str(datetime.datetime.now().year)):
                        save_table = True
                case title if spaceless_titles[11] in spaceless_title:
                    # Basic cleaning
                    line = line.strip()
                    while "  " in line:
                        line = line.replace("  ", " ")
                    # Set the table title becasue it appears on multiple rows
                    if not title_set:
                        title = "Number of Confirmed Deaths Where Xylazine Toxicity was Part of the Cause of Death"
                        title_set = True
                        # Manually set the header
                        header = "Year DeathsWhereXylazineToxicityWasSoleCause DeathsWhereCombinedToxicityWithXylazineWasCause Total"
                        # Add the header to the table
                        table = table._append(pandas.Series(header.split(" ")), ignore_index=True)
                    if not line[0].isnumeric():
                        skip_line = True
                    if line.startswith(str(datetime.datetime.now().year)):
                        save_table = True
            # Append the line to the table
            if not skip_line:
                table = table._append(pandas.Series(line.split(" ")), ignore_index=True)
        # If we find the end of the table
        elif ((not searching) and (line in end_of_table) and (not ignore_next) and (skip_until == 0)) or save_table:
            # If the title is not the same as the last one (ie. a new table), then add the table to the list
            if previous_title.strip().replace(" ", "").replace("–", "-") != title.strip().replace(" ", "").replace("–", "-") :
                # Add the title to the table so we know what data it is
                table.style.set_caption(title)
                # add the table to the list of tables
                report_tables.append(table)
            # If the titles do match, and the table is the place of death breakdown, then we need to actually remove the first two rows as headers
            elif previous_title.strip().replace(" ", "").replace("–", "-") == title.strip().replace(" ", "").replace("–", "-") and (spaceless_titles[9] and "breakdown" in spaceless_title):
                report_tables[-1] = pandas.concat([report_tables[-1], table.iloc[2:]], ignore_index=0)
            # If the titles do match, then instead concatinate the new table and the last table in the list
            else:
                report_tables[-1] = pandas.concat([report_tables[-1], table.iloc[1:]], ignore_index=0)
            # Reset the searching flag
            searching = True
            title_set = False
            previous_title = title
            save_table = False
        elif ignore_next:
            ignore_next = False
            continue
        elif skip_until > 0:
            if line_index == skip_until:
                skip_until = 0
            continue
        else:
            continue

    # Save the dataframes to an excel file
    print("Saving the data to an excel file...")
    with pandas.ExcelWriter(os.path.join(output_dir, f"{date_of_report}_skPubCentre.xlsx")) as writer:
        for index, table in enumerate(report_tables):
            table.to_excel(writer, sheet_name=f"Table {index + 1}", index=False)

    # Cleanup the raw data file
    if not dont_delete:
        os.remove(raw_data_file)
        print("The raw data has been deleted, and cleanup is complete!")




# Test code below
if __name__ == "__main__":
    driver = start_driver(headless=True)
    sk_pubcentre_scrape(driver, 25)
    driver.quit()