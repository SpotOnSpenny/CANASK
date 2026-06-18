# Python Standard Library Dependencies
import os
import json
import datetime
import logging
import re

# External Dependency Imports
import pandas

logger = logging.getLogger(__name__)

#######################################################################################
#                                        Notes:                                       #
# For now, these functions include the cleaning of the dataframes required to create  #
# the visualization. In the future, a big #TODO will be to remove this step and       #
# include it in either separate scripts that pass the data to a database after        #
# scraping, or directly in the scraping scripts themselves.                           #
#######################################################################################

# Helper function to pull data from the specified excel/csv file
def pull_data(data_source: list):
    sheets = {}
    for source in data_source:
        output_dir = os.path.join(os.path.abspath(os.path.dirname(os.path.dirname(__file__))), "output")
        if any(source in file for file in os.listdir(output_dir)):
            file = [file for file in os.listdir(output_dir) if source in file][0]
            match file.split(".")[-1]:
                case "csv":
                    sheets[source] = {
                        "date_updated": datetime.datetime.strptime(file.split("_")[0], "%Y%m%d").strftime("%B %d, %Y"),
                        "data_until": datetime.datetime.strptime(file.split("_")[1], "%Y%m%d").strftime("%B %d, %Y") if len(file.split("_")) > 1 else None,
                        "dataframe": pandas.read_csv(os.path.join(output_dir, file))
                        }
                case "xlsx":
                    # Specific handling for ontario data
                    if "onODPRN" in file.split("_")[1]:
                        dataframes = pandas.read_excel(os.path.join(output_dir, file), sheet_name=None)
                        for name, dataframe in dataframes.items():
                            dataframe.set_flags(allows_duplicate_labels=False)
                            dataframe.dropna(axis=0, inplace=True)
                            sheets[name] = {
                                "date_updated": datetime.datetime.strptime(file.split("_")[0], "%Y%m%d").strftime("%B %d, %Y"),
                                "dataframe": dataframe
                                }
                    # Handling for other xlsx files
                    else:
                        dataframes = pandas.read_excel(os.path.join(output_dir, file), engine='calamine', sheet_name=None).values()
                        for dataframe in dataframes:
                            name = list(filter(lambda value: True if "Unnamed" not in value and value != "NaN" else False, dataframe.columns))[0]
                            dataframe.set_flags(allows_duplicate_labels=False)
                            dataframe.columns = dataframe.iloc[0]
                            dataframe.dropna(axis=0, inplace=True)
                            dataframe = dataframe.drop(dataframe.columns[[0]], axis=1).reset_index(drop=True)
                            if file.split("_")[1].isdigit():
                                try: # Try the full date format
                                    data_until = datetime.datetime.strptime(file.split("_")[1], "%Y%m%d").strftime("%B %d, %Y")
                                except ValueError: # If it fails, try the year only format
                                    data_until = datetime.datetime.strptime(file.split("_")[1], "%Y%m").strftime("%B, %Y")
                            else:
                                data_until = file.split("_")[0]
                            sheets[name] = {
                                "date_updated": datetime.datetime.strptime(file.split("_")[0], "%Y%m%d").strftime("%B %d, %Y"),
                                "data_until": data_until,
                                "dataframe": dataframe
                                }
        else:
            raise FileNotFoundError(f"Data source {source} not found in the output directory!")
    return sheets

# Helper function to pull the data from the provided source into a dataframe
# Use exact_match to determine if the seach should be looking for the exact title (ie, return a single, exact dataframe for each term)
# or if it should be looking for any dataframe that contains the term (ie, return all dataframes that contain the term)
def filter_data(data: dict, find_these: list, exact_match: bool = False):
    dataframes = []
    match exact_match:
        case True:
            for key in data.keys():
                if any(find_this.split(",")[0].lower().replace(" ", "") == key.split(",")[0].lower().replace(" ", "") for find_this in find_these):
                    dataframes.append(data[key])
        case False:
            for key in data.keys():
                if any(find_this.split(",")[0].lower().replace(" ", "") in key.split(",")[0].lower().replace(" ", "") for find_this in find_these):
                    data[key]["Name"] = key
                    dataframes.append(data[key])
    return dataframes

# Restructured functions to generate the visual by page, not by graph
# Note that we do have stimulant data here that we can expand this for in the future
def export_nat_drug_toxicity_deaths():
    # Find what national data exists and clean it up to get what we need (toxicity deaths by province by year)
    national_raw = pull_data(["nationalHealthInfobase"])
    provincial_dfs = {}
    raw_df = national_raw["nationalHealthInfobase"]["dataframe"]
    provinces = ["British Columbia", "Alberta", "Saskatchewan", "Manitoba", "Ontario", "Quebec", "New Brunswick", "Nova Scotia", "Prince Edward Island", "Newfoundland and Labrador"]
    deaths_filter = raw_df["Source"] == "Deaths"
    stat_filter = raw_df["Specific_Measure"] == "Overall numbers"
    period_filter = raw_df["Time_Period"] == "By year"
    unit_filter = raw_df["Unit"] == "Number"
    substance_filter = raw_df["Substance"] == "Opioids"
    for province in provinces:
        province_filter = raw_df["Region"] == province
        provincial_dfs[province] = {}
        provincial_dfs[province]["sources"] = [{
            "name": "Opioid- and Stimulant-related Harms in Canada",
            "last_updated": national_raw["nationalHealthInfobase"]["date_updated"],
            "url": "https://health-infobase.canada.ca/substance-related-harms/opioids-stimulants/"
        }]
        provincial_dfs[province]["data"] = raw_df[deaths_filter & stat_filter & period_filter & unit_filter & province_filter & substance_filter]
        # Limit the column to deaths and year
        provincial_dfs[province]["data"] = provincial_dfs[province]["data"][["Year_Quarter", "Value"]]
        provincial_dfs[province]["data"].rename(columns={"Year_Quarter": "Year"}, inplace=True)
    
    # Check the years to see if any provinces have a month range (usually the most recent year does), so that we can append that info to the about these data
    provincial_month_ranges = {}
    for province in provinces:
        for index, row in provincial_dfs[province]["data"].iterrows():
            if "to" in row["Year"]:
                range = f"{row["Year"].split("(")[1].replace(")", "").strip()}_{row["Year"].split("(")[0].strip()}"
                if range not in provincial_month_ranges.keys():
                    provincial_month_ranges[range] = province
                else:
                    provincial_month_ranges[range] += f", {province}"
                row["Year"] = row["Year"].split(" ")[0].strip()

    # # Replace national data with what provincial data we have and make note of the replaced data points for an about this data section
    # # Commenting this out for now, as we're not certain the definitions of "toxicity deaths" match between national and provincial sources
    # # Work is being done among coroners services to harmonize these definitions, so it may be possible to reuse this in the future.
    # sask_raw = pull_data(["skPubCentre"])
    # sask_filtered = filter_data(sask_raw, ["ConfirmedDrugToxicityDeathsbyMannerofDeath"])
    # sask_total_deaths = sask_filtered[0]["dataframe"].loc[sask_filtered[0]["dataframe"]["Year"] == "Total"]
    # provincial_dfs["Saskatchewan"]["sources"] = [{
    #     "name": "Saskatchewan Coroners Service",
    #     "last_updated": sask_filtered[0]["date_updated"],
    #     "url": "https://publications.saskatchewan.ca/#/products/90505"
    # }]
    # for column_name, column_data in sask_total_deaths.iloc[:, 1:].items():
    #     column_value = column_data.to_list()[0]
    #     mask = provincial_dfs["Saskatchewan"]["data"]["Year"].str.contains(re.escape(column_name), case=False, na=False)
    #     provincial_dfs["Saskatchewan"]["data"].loc[mask, "Value"] = column_value
    #     provincial_dfs["Saskatchewan"]["data"].loc[mask, "Year"] = column_name

    # bc_raw = pull_data(["bcCoronersReport"])
    # bc_filtered = filter_data(bc_raw, ["Unregulated Drug Deaths by Month"])
    # bc_total_deaths = bc_filtered[0]["dataframe"].iloc[-1]
    # provincial_dfs["British Columbia"]["sources"] = [{
    #     "name": "BC Coroners Service",
    #     "last_updated": bc_filtered[0]["date_updated"],
    #     "url": "https://app.powerbi.com/view?r=eyJrIjoiM2Y5YzRjNzQtMzAyNS00NWFiLWI3MDktMzI5NWQ3YmVhNmZjIiwidCI6IjZmZGI1MjAwLTNkMGQtNGE4YS1iMDM2LWQzNjg1ZTM1OWFkYyJ9"
    # }]
    # provincial_dfs["British Columbia"]["data"]["Year"] = provincial_dfs["British Columbia"]["data"]["Year"].str.strip()
    # for column_name, column_data in bc_total_deaths.iloc[1:].items():
    #     mask = provincial_dfs["British Columbia"]["data"]["Year"].str.contains(re.escape(column_name.strip()), case=False, na=False)
    #     if mask.any():
    #         provincial_dfs["British Columbia"]["data"].loc[mask, "Value"] = column_data
    #         provincial_dfs["British Columbia"]["data"].loc[mask, "Year"] = column_name
    
    # Export the lines in a json file which includes:
        # The date each data source was last edited
        # A line of data for each province
        # A blurb with variables to be used in the about this data section
    province_keys = {
        "British Columbia": "bc",
        "Alberta": "ab",
        "Saskatchewan": "sk",
        "Manitoba": "mb",
        "Ontario": "on",
        "Quebec": "qc",
        "New Brunswick": "nb",
        "Nova Scotia": "ns",
        "Prince Edward Island": "pe",
        "Newfoundland and Labrador": "nl"
    }
    sources = []
    total_tox_deaths_data = {
        "x_axes": {},
        "y_axes": {},
        "y_axes_per_100k": {}
    }
    longest_year_line = []
    for province_data in provincial_dfs:
        for source in provincial_dfs[province_data]["sources"]:
            if source["name"] not in [source["name"] for source in sources]:
                if source["name"] == "Opioid- and Stimulant-related Harms in Canada":
                    source["Province"] = "all provincial data"
                else:
                    source["Province"] = province_data
                sources.append(source)
        province_abbreviation = province_keys[province_data]
        total_tox_deaths_data["y_axes"][f"{province_abbreviation}_line_y"] = provincial_dfs[province_data]["data"]["Value"].to_list()
        if len(provincial_dfs[province_data]["data"]["Year"].to_list()) > len(longest_year_line):
            longest_year_line = provincial_dfs[province_data]["data"]["Year"].to_list()
    total_tox_deaths_data["x_axes"]["can_line_x"] = [year.replace(u"\xa0", "") for year in longest_year_line]
    total_tox_deaths_data["sources"] = sources
    if len(provincial_month_ranges.keys()) > 0:
        incomplete_message = "<br><br>Please note that at this time some reports are unpublished, or only contain partial data for some years. The following provinces have incomplete data:<br> "
        for month_range, provinces_list in provincial_month_ranges.items():
            incomplete_message += f"{provinces_list} contain data only for {month_range.split("_")[0]} in the year {month_range.split("_")[1]}."
    total_tox_deaths_data["about_these_data"] = f"""These data are collected from the national Opioid- and Stimulant-related Harms in Canada, however, similar data is also available within provincial reports and reported on the provincial pages within this tool due to differing definitions of what constitutes a toxicity death among provincial coroners reports. Kindly refer to the "last updated" date for information on when the data for each source was last published. Additionally, some data from this source have been suppressed (in the tabular data below as "Suppr."), and appear as 0's within the visual above. For more information about what data has been suppressed and why, please visit the data source directly and review their Technical Notes.
    {incomplete_message}<br><br>
    The data sources used in this visualization are as follows:"""

    # Create the statistics for the /100,000 population
    # Get the population data
    population_data = pull_data(["nationalPopulationData"])
    population_data = filter_data(population_data, ["nationalPopulationData"])
    population_df = population_data[0]["dataframe"]
    
    # diivide each popululation by 100,000
    for province in provinces:
        for index, year in enumerate(total_tox_deaths_data["x_axes"]["can_line_x"]):
            population = population_df.loc[population_df["GEO"] == province].loc[population_df["REF_DATE"] == int(year.split(" ")[0])]["VALUE"].values[0]
            hundred_k = population / 100000
            total_tox_deaths_data["y_axes_per_100k"][f"{province_keys[province]}_line_y_per_100k"] = [round((float(value) / hundred_k), 2) if value.isnumeric() else 0 for value in total_tox_deaths_data["y_axes"][f"{province_keys[province]}_line_y"]]
   
    # Add population data to sources
    sources.append({
        "name": "Statistics Canada",
        "last_updated": population_data[0]["date_updated"][-4:],
        "url": "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1710000501",
        "Province": "population data for all provinces"
    })

    # Export the data to a json file
    with open(os.path.join(os.path.abspath(os.path.dirname(__file__)), "static/js/total_tox_deaths_data.json"), "w") as file:
        json.dump(total_tox_deaths_data, file)

def export_on_visual_data():
    data = pull_data(["onODPRN"])
    on_dataframes = filter_data(data, ["Provincial Drug Toxicity", "PHU Confirmed & Probable"])
    graph_data = {}
    up_to_date_until = None
    # Filter the drug toxicity in PHU by month into years
    toxicity_phu_data = {
        "data last updated": datetime.datetime.strptime(on_dataframes[1]["date_updated"], "%B %d, %Y").strftime("%Y%m%d")
    }
    dates = None
    # Iterate over columns
    for series_name, series in on_dataframes[1]["dataframe"].items():
        if series_name == "date":
            dates = series
            # Get the last date in the series
            last_date = f"{dates.iloc[-1]}01"
            if int(toxicity_phu_data["data last updated"]) > int(last_date):
                up_to_date_until = datetime.datetime.strptime(last_date, "%Y%m%d").strftime("%B, %Y")
        else:
            x_axes = []
            y_axes = []
            year_total = 0
            year = 2018
            for index, row in series.items():
                if str(year) in str(dates[index]):
                    year_total += row
                else:
                    x_axes.append(year)
                    y_axes.append(year_total)
                    year_total = row
                    year += 1
            # Append the last year
            x_axes.append(year)
            y_axes.append(year_total)
            toxicity_phu_data[series_name] = {
                "x": x_axes,
                "y": y_axes,
                "up to date until": up_to_date_until
            }
    toxicity_phu_data["data last updated"] = datetime.datetime.strptime(on_dataframes[1]["date_updated"], "%B %d, %Y").strftime("%B, %Y")

    # Filter the data for procincial drug toxicity by year
    provincial_toxicity_deaths = {
        "data last updated": datetime.datetime.strptime(on_dataframes[0]["date_updated"], "%B %d, %Y").strftime("%Y%m%d")
    }
    years = None
    months = None
    all_drug_deahts = []
    up_to_date_until = provincial_toxicity_deaths["data last updated"]
    for series_name, series in on_dataframes[0]["dataframe"].items():
        if series_name == "year":
            years = series
        elif series_name == "month":
            months = series
            # Get the last date in the series
            last_date = f"{years.iloc[-1]}{months.iloc[-1]}01"
            if int(up_to_date_until) > int(last_date):
                up_to_date_until = last_date
        else:
            x_axes = []
            y_axes = []
            year_total = 0
            year = 2018
            for index, row in series.items():
                if row == "*":
                    new_up_to_date_until = f"{years[index]}{months[index]}01"
                    if int(up_to_date_until) > int(new_up_to_date_until):
                        up_to_date_until = new_up_to_date_until
                elif str(year) in str(years[index]):
                    year_total += row
                else:
                    x_axes.append(year)
                    y_axes.append(year_total)
                    year_total = row
                    year += 1
            # Append the last year
            x_axes.append(year)
            y_axes.append(year_total)
            provincial_toxicity_deaths[series_name] = {
                "x": x_axes,
                "y": y_axes,
                "up to date until": datetime.datetime.strptime(up_to_date_until, "%Y%m%d").strftime("%B, %Y")
            }
    provincial_toxicity_deaths["data last updated"] = datetime.datetime.strptime(on_dataframes[0]["date_updated"], "%B %d, %Y").strftime("%B, %Y")
    # Add up matching indexes for each year to get the total deaths
    for index in range(len(provincial_toxicity_deaths["opioid confirmed"]["x"])):
        all_drug_deahts.append(provincial_toxicity_deaths["opioid confirmed"]["y"][index] + provincial_toxicity_deaths["stimulant"]["y"][index] + provincial_toxicity_deaths["opioid probable"]["y"][index] + provincial_toxicity_deaths["other drug"]["y"][index])
    provincial_toxicity_deaths["all drugs"] = {
        "x": provincial_toxicity_deaths["opioid confirmed"]["x"],
        "y": all_drug_deahts,
        "up to date until": datetime.datetime.strptime(up_to_date_until, "%Y%m%d").strftime("%B, %Y")
    }


    graph_data["toxicity_phu_data"] = toxicity_phu_data
    graph_data["provincial_toxicity_deaths"] = provincial_toxicity_deaths
    with open("static/js/on_vis.json", "w") as file:
        json.dump(graph_data, file)

def v1_drugchecking_export_clean(writer, province):
    # Pan-Canadian drug-checking harmonized data: one row per checked sample, spanning provinces.
    # New-style cleaner -- emits a category_treemap (Category -> Expected Drug, Province + Site as
    # geo levels) straight to the writer, no intermediate block dict. `province` is the target scope
    # key ("canada"); the per-sample Province lives in the geo composite.
    pulled = pull_data(["drugChecking"])["drugChecking"]
    df = pulled["dataframe"].copy()
    # The raw headers carry stray leading/trailing and double spaces (e.g. "Visit Date ",
    # "Expected Drug Category  (1)"); normalize whitespace so the column refs below are clean.
    df.columns = df.columns.str.strip().str.replace(r"\s+", " ", regex=True)
    # Province arrives both abbreviated and spelled out; canonicalize known abbreviations so the
    # Province dropdown groups them as one (extend this map as new provinces appear).
    df["Province"] = df["Province"].replace({"Sask": "Saskatchewan"})

    v = writer.visual(province, "checked_samples_by_expected_drug")
    if v is None:
        return   # no definition yet (run `define-visuals`); writer already warned
    v.use_source({
        "name": "Pan-Canadian Drug Checking Data Harmonization",
        "about": """
This data is collected by individual organizations across Canada, and provided to the CCSA working towards the goal of data harmonization across drug checking sites.

To find out more about drug checking, and the CCSA's Drug Checking working group, please visit the link below:
        """,
        "link": "https://www.ccsa.ca/en/data-trends/drug-checking",
        "last_updated": pulled["date_updated"],
        "data_until": pulled["data_until"],
    })

    # Drop rows missing any grouping key, then parse "Visit Date" (M/D/YYYY) to a "YYYY-MM" month
    # key -- the same month grain the BC treemap uses (client derives year/seasonal/all-time).
    df = df.dropna(
        subset=["Site/Organization", "Province", "Expected Drug Category (1)", "Expected Drug (1)"]).copy()
    df["_month"] = pandas.to_datetime(
        df["Visit Date"], format="%m/%d/%Y", errors="coerce").dt.strftime("%Y-%m")
    df = df[df["_month"].notna()]
    # Geo levels ordered broad -> narrow ("Province||Site") so the client's cascade is Province then
    # Site; matches manifest geo_levels=["Province", "Site/Organization"]. Types (metric/geo_type/
    # dimension*) come from the Visuals row via the writer -- cleaning only supplies values.
    for (prov, site), site_df in df.groupby(["Province", "Site/Organization"]):
        geo = f"{prov}||{site}"
        counts = site_df.groupby(["_month", "Expected Drug Category (1)", "Expected Drug (1)"]).size()
        for (month, category, drug), n in counts.items():
            v.fact(geo, month, int(n), dimension=category, dimension2=drug, time_frame_type="month")


def v1_BC_export_clean():
    # Pull up data from the BC Coroners Service and BC Drug Sense
    data = pull_data(["bcCoronersReport", "bcDrugSense"])

    # ---- Clean Data for Unregulated Drug Deaths Heatmap ----
    # Filter data out for the heatmap of unregulated drug deaths by health authority
    bc_coroners = filter_data(data, ["Unregulated Drug Deaths by Health Authority of Injury", "Unregulated Drug Death Rates per 100,000 by Health Authority of Injury"], True)
    # Create the data structure for the heatmap
    heatmap_data = {
        "data_source": {
            "name": "BC Coroners Service",
            "about": """
This data has been collected by the British Columbia Coroners Service (BCCS),and is based on toxicology reports from individuals who have died in British Columbia where the cause of death was determined to be unregulated drugs and/or drugs sold illicitly,and does not include deaths related to an individuals prescribed drugs,or intentional deaths due to toxicity.The data is updated monthly by the BCCS.

For more information,visit the BCCS website by clicking the button below:
            """,
            "link": "https://app.powerbi.com/view?r=eyJrIjoiNjhiYjgxYzUtYjIyOC00ZGQ2LThhMzEtOWU5Y2Q4YWI0OTc5IiwidCI6IjZmZGI1MjAwLTNkMGQtNGE4YS1iMDM2LWQzNjg1ZTM1OWFkYyJ9",
            "last_updated": bc_coroners[0]["date_updated"],
            "data_until": bc_coroners[0]["data_until"]
        },
        "data": {
            "counts": {},
            "rates": {}
        },
        "visual_options": {
            "heatmap-title": "Unregulated Drug Deaths in British Columbia by Health Authority",
            "table-title": "Unregulated Drug Deaths in replace_with_health_authority Health Authority",
            "table-row-title": "replace_location",
        }
    }
    # Format the years for the x axis
    years = [str(year).replace(u"\xa0", "") for year in bc_coroners[0]["dataframe"].columns.to_list()[1:]]
    # Count data
    for index, row in bc_coroners[0]["dataframe"].iterrows():
        # Create the x and y axes for the rates
        heatmap_data["data"]["counts"][row["HA_Name\xa0"]] = {
            "x": years,
            "y": bc_coroners[0]["dataframe"].iloc[index].to_list()[1:]
        }
    # Rate data
    for index, row in bc_coroners[1]["dataframe"].iterrows():
        # Create the x and y axes for the rates
        heatmap_data["data"]["rates"][row["Health Authority\xa0"]] = {
            "x": years,
            "y": bc_coroners[1]["dataframe"].iloc[index].to_list()[1:]
        }

    # ----- Clean Data for Unregulated Drug Deaths by Sex -----
    # pull and filter the data needed
    bc_coroners = filter_data(data, ["Sex-Specific Unregulated Drug Death Rates", "Unregulated Drug Deaths by Sex"], False)
    
    # Create the data structure for the line chart
    death_by_sex_data = {
        "data_source": {
            "name": "BC Coroners Service",
            "about": """
This data has been collected by the British Columbia Coroners Service (BCCS),and is based on toxicology reports from individuals who have died in British Columbia where the cause of death was determined to be unregulated drugs and/or drugs sold illicitly,and does not include deaths related to an individuals prescribed drugs,or intentional deaths due to toxicity.The data is updated monthly by the BCCS.

For more information,visit the BCCS website by clicking the button below:
            """,
            "link": "https://app.powerbi.com/view?r=eyJrIjoiNjhiYjgxYzUtYjIyOC00ZGQ2LThhMzEtOWU5Y2Q4YWI0OTc5IiwidCI6IjZmZGI1MjAwLTNkMGQtNGE4YS1iMDM2LWQzNjg1ZTM1OWFkYyJ9",
            "last_updated": bc_coroners[0]["date_updated"],
            "data_until": bc_coroners[0]["data_until"]
        },
        "data": {
            "counts": {},
            "rates": {}
        },
        "visual_options":{
            "rates-title": "Sex-Specific Unregulated Drug Deaths per 100,000 Population in the replace_with_health_authority Health Authority",
            "counts-title": "Sex-Specific Unregulated Drug Deaths in the replace_with_health_authority Health Authority",
            "table-title": "Sex-Specific Unregulated Drug Deaths in replace_with_health_authority Health Authority",
            "rates-y-axis-title": "Unregulated Drug Deaths per 100,000 Population",
            "counts-y-axis-title": "Unregulated Drug Deaths",
            "table-rates-row": "replace_me deaths/100,000",
            "table-counts-row": "replace_me deaths",
        }
    }

    # Iterate over each dataframe and pull the data we need
    for dataframe in bc_coroners:
        # Get the dataframe and the name of the dataframe
        df = dataframe["dataframe"]
        health_authority = dataframe["Name"].split(":")[0].strip().replace(" Health Authority", "")
        df_name = dataframe["Name"].split(":")[1].strip()
        if health_authority == "British Columbia Health Authority":
            continue

        if "rates" in df_name.lower():
            death_by_sex_data["data"]["rates"][health_authority] = {
                "x": [str(year).replace(u"\xa0", "") for year in df.columns.to_list()[1:]],
                "female_y": df.iloc[0].to_list()[1:],
                "male_y": df.iloc[1].to_list()[1:]
            }
        else:
            death_by_sex_data["data"]["counts"][health_authority] = {
                "x": [str(year).replace(u"\xa0", "") for year in df.columns.to_list()[1:]],
                "female_y": df.iloc[0].to_list()[1:],
                "male_y": df.iloc[1].to_list()[1:],
                "total_y": df.iloc[2].to_list()[1:]
            }

    # ----- Drug Toxicity Deaths by Drug Type -----
    drug_toxicity_deaths_by_type = {
        "data_source": {
            "name": "BC Coroners Service",
            "about": """
This data has been collected by the British Columbia Coroners Service (BCCS),and is based on toxicology reports from individuals who have died in British Columbia where the cause of death was determined to be unregulated drugs and/or drugs sold illicitly,and does not include deaths related to an individuals prescribed drugs,or intentional deaths due to toxicity.The data is updated monthly by the BCCS.

For more information,visit the BCCS website by clicking the button below:
            """,
            "link": "https://app.powerbi.com/view?r=eyJrIjoiNjhiYjgxYzUtYjIyOC00ZGQ2LThhMzEtOWU5Y2Q4YWI0OTc5IiwidCI6IjZmZGI1MjAwLTNkMGQtNGE4YS1iMDM2LWQzNjg1ZTM1OWFkYyJ9",
            "last_updated": bc_coroners[0]["date_updated"],
            "data_until": bc_coroners[0]["data_until"]
        },
        "data": {
            "counts": {},
            "rates": {},
            "percentages": {}
        },
        "visual_options":{
            "percentages-title": "Percent of Unregulated Drug Deaths in British Columbia Attributed to Drugs Relevant to Death by Year",
            "rates-title": "Unregulated Drug Deaths per 100,000 Population in British Columbia by Year and Drugs Relevant to Death",
            "counts-title": "Unregulated Drug Deaths in British Columbia by Year and Drugs Relevant to Death",
            "table-title": "Unregulated Drug Deaths in British Columbia by Year and Drugs Relevant to Death",
            "rates-y-axis-title": "Unregulated Drug Deaths Caused by Drug per 100,000 Population",
            "counts-y-axis-title": "Unregulated Drug Deaths Caused by Drug",
            "percentages-y-axis-title": "Percent of Unregulated Drug Deaths Caused by Drug",
            "table-rates-row": "Unregulated Drug Deaths Caused by replace_me/100,000 Population",
            "table-percentages-row": "Percent of Unregulated Drug Deaths Caused by replace_me",
            "table-counts-row": "Unregulated Drug Deaths Caused by replace_me",
            "hover-type": "x unified",
            "hover-info": "default"
        }
}

    # Get the population data for the rates
    population_data = pull_data(["nationalPopulationData"])
    bc_population_data = filter_data(population_data, ["nationalPopulationData"])[0]["dataframe"]
    bc_population_data = bc_population_data.loc[bc_population_data["GEO"] == "British Columbia"]
    
    # Pull the list of drugs
    bc_coroners = filter_data(data, ["Unregulated Drug Deaths byDrug Types Relevant to Death"])
    drugs = [drug for drug in bc_coroners[0]["dataframe"].iloc[:, 0].values]
    total_deaths = heatmap_data["data"]["counts"]["British Columbia"]
    for dataframe in bc_coroners:
        if dataframe["Name"] == "Unregulated Drug Deaths by Drug Types Relevant to Death":
            dataframe = dataframe["dataframe"]
            years = [str(year).replace(u"\xa0", "") for year in dataframe.columns.to_list()[1:]]
            drug_toxicity_deaths_by_type["data"]["rates"]["x"] = years
            drug_toxicity_deaths_by_type["data"]["percentages"]["x"] = years
            drug_toxicity_deaths_by_type["data"]["counts"]["x"] = years
            break

    # Iterate over each drug and pull the data we need
    for drug in drugs:
        drug_data = dataframe.loc[dataframe.iloc[:, 0] == drug].iloc[0, 1:]
        drug_data = [float(value.replace(u"\xa0", "").replace("%", "")) if isinstance(value, str) else value for value in drug_data]
        drug_toxicity_deaths_by_type["data"]["percentages"][f"{drug}_y"] = drug_data
        drug_toxicity_deaths_by_type["data"]["counts"][f"{drug}_y"] = [round(drug_data[index] * int(total_deaths["y"][index]) / 100) for index in range(len(drug_data))]
        drug_toxicity_deaths_by_type["data"]["rates"][f"{drug}_y"] = [round((drug_toxicity_deaths_by_type["data"]["counts"][f"{drug}_y"][index] / float(bc_population_data.loc[bc_population_data["REF_DATE"] == int(years[index]), "VALUE"].values[0]) * 100000), 2) for index in range(len(drug_data))]

    # ----- Unregulated Drug Toxicity Deaths by Age Group -----
        drug_toxicity_deaths_by_age = {
        "data_source": {
            "name": "BC Coroners Service",
            "about": """
This data has been collected by the British Columbia Coroners Service (BCCS),and is based on toxicology reports from individuals who have died in British Columbia where the cause of death was determined to be unregulated drugs and/or drugs sold illicitly,and does not include deaths related to an individuals prescribed drugs,or intentional deaths due to toxicity.The data is updated monthly by the BCCS.

For more information,visit the BCCS website by clicking the button below:
            """,
            "link": "https://app.powerbi.com/view?r=eyJrIjoiNjhiYjgxYzUtYjIyOC00ZGQ2LThhMzEtOWU5Y2Q4YWI0OTc5IiwidCI6IjZmZGI1MjAwLTNkMGQtNGE4YS1iMDM2LWQzNjg1ZTM1OWFkYyJ9",
            "last_updated": bc_coroners[0]["date_updated"],
            "data_until": bc_coroners[0]["data_until"]
        },
        "data": {
            "counts": {},
            "rates": {},
        },
        "visual_options":{
            "rates-title": "Annual Unregulated Drug Deaths per 100,000 Population in British Columbia by Age Group",
            "counts-title": "Annual Unregulated Drug Deaths in British Columbia by Age Group",
            "table-title": "Annual Unregulated Drug Deaths in British Columbia by Age Group",
            "rates-y-axis-title": "Unregulated Drug Deaths per 100,000 Population",
            "counts-y-axis-title": "Unregulated Drug Deaths",
            "table-rates-row": "Unregulated Drug Deaths for Those replace_me/100,000 Population",
            "table-counts-row": "Unregulated Drug Deaths among for Those replace_me",
        },
        "additional_rows": {
            "Total Deaths": []
        }
}
    bc_coroners = filter_data(data, ["Unregulated Drug Deaths by Age Group", "Age-Specific Unregulated Drug Death Rates per 100,000 Population"])
    bc_coroners = bc_coroners[:2] # NOTE the other two dataframes pulled and discluded here are monthly data which we can use later on if we'd like
    for dataframe in bc_coroners:
        if "Unregulated Drug Deaths by Age Group" in dataframe["Name"]:
            years = [str(year).replace(u"\xa0", "") for year in dataframe["dataframe"].columns.to_list()[1:]]
            working_frame = dataframe["dataframe"]
            drug_toxicity_deaths_by_age["data"]["counts"]["x"] = years
            for index, row in working_frame.iterrows():
                if "Total" in row.iloc[0]:
                    drug_toxicity_deaths_by_age["additional_rows"]["Total Deaths"] = [str(value).replace(u"\xa0", "") if value.replace(u"\xa0", "") != "" != "" or u"\xa0" else 0 for value in row.to_list()[1:]]
                    continue
                age_group = row.iloc[0].replace(u"\xa0", "") if row.iloc[0] != "Not available" else "Age Unavailable"
                drug_toxicity_deaths_by_age["data"]["counts"][f"{age_group}_y"] = [str(value).replace(u"\xa0", "") if value.replace(u"\xa0", "") != "" else 0 for value in row.to_list()[1:]]
        if "Rates" in dataframe["Name"]:
            working_frame = dataframe["dataframe"]
            years = [str(year).replace(u"\xa0", "") for year in working_frame.columns.to_list()[1:]]
            drug_toxicity_deaths_by_age["data"]["rates"]["x"] = years
            for index, row in working_frame.iterrows():
                age_group = row.iloc[0].replace(u"\xa0", "")
                drug_toxicity_deaths_by_age["data"]["rates"][f"{age_group}_y"] = [str(value).replace(u"\xa0", "") if value.replace(u"\xa0", "") != "" != "" or u"\xa0" else 0 for value in row.to_list()[1:]]

    # ----- Prep BC Drug Sense data for use in several visuals -----
    bc_drug_sense = data["bcDrugSense"]["dataframe"]
    last_updated = data["bcDrugSense"]["date_updated"]
    data_until = data["bcDrugSense"]["data_until"]
    # Separate the data by year
    data_by_year = {}
    starting_year = 2018
    current_year = int(datetime.datetime.strptime(data_until, "%B %d, %Y").year)
    range_years = range(starting_year, current_year + 1)

    for year in range_years:
        data_by_year[str(year)] = bc_drug_sense.loc[bc_drug_sense["Visit Date"].str.contains(str(year))]

    # ----- Clean Data for Drug Supply by Year -----
    drug_supply_by_year = {
        "data_source": {
            "name": "British Columbia Centre for Substance Use (BCCSU)",
            "about": """
This data is collected from the British Columbia Centre on Substance Use (BCCSU) and is based on voluntary drug testing results.The data is collected from samples provided by individuals and organizations in British Columbia.The data is collected to help inform the public about the drug supply in British Columbia and to help inform harm reduction strategies.Please note that this data is not representative of the entire illicit drug supply in British Columbia,but rather provides a snapshot of the drug supply based on voluntary submissions.

For more information visit the BCCSU's Drug Sense website by clicking the button below:
            """,
            "link": "https://drugsense.bccsu.ubc.ca/",
            "last_updated": last_updated,
            "data_until": data_until
        },
        "data": {
            "counts": {},
            "rates": {}
        },
        "visual_options":{
            "rates-title": "Percent of Submitted Samples Belonging to Major Drug Categories in British Columbia by Year",
            "counts-title": "Number of Submitted Samples Belonging to Major Drug Categories in British Columbia by Year",
            "table-title": "Major Drug Categories in Submitted Samples by Year",
            "rates-y-axis-title": "Percent of Samples Belonging to Category of Drug",
            "counts-y-axis-title": "Number of Samples Belonging to Category of Drug",
            "table-rates-row": "Percent of Samples Classified as replace_me",
            "table-counts-row": "Number of Samples Classified as replace_me",
        },
        "additional_rows": {
            "Total Samples": []
        }
    }

    drug_categories = bc_drug_sense["Category"].unique()
    drug_supply_by_year["data"]["counts"]["x"] = [year for year in data_by_year.keys()]
    drug_supply_by_year["data"]["rates"]["x"] = [year for year in data_by_year.keys()]
    for category in drug_categories:
        drug_supply_by_year["data"]["counts"][f"{category}_y"] = []
        drug_supply_by_year["data"]["rates"][f"{category}_y"] = []
        for year, data in data_by_year.items():
            category_data = data.loc[data["Category"] == category]
            drug_supply_by_year["data"]["counts"][f"{category}_y"].append(len(category_data))
            drug_supply_by_year["data"]["rates"][f"{category}_y"].append(round((len(category_data)/len(data) * 100), 2))
    # Add the total samples to the additional rows
    drug_supply_by_year["additional_rows"]["Total Samples"] = [len(data) for data in data_by_year.values()]

    # ---- Clean Data for Presence of Fentanyl and Benzodiazepines by Year -----
    fent_benz_by_year = {
        "data_source": {
            "name": "British Columbia Centre for Substance Use (BCCSU)",
            "about": """
This data is collected from the British Columbia Centre on Substance Use (BCCSU) and is based on voluntary drug testing results.The data is collected from samples provided by individuals and organizations in British Columbia.The data is collected to help inform the public about the drug supply in British Columbia and to help inform harm reduction strategies.Please note that this data is not representative of the entire illicit drug supply in British Columbia,but rather provides a snapshot of the drug supply based on voluntary submissions.

For more information visit the BCCSU's Drug Sense website by clicking the button below:
            """,
            "link": "https://drugsense.bccsu.ubc.ca/",
            "last_updated": last_updated,
            "data_until": data_until
        },
        "data": {
            "counts": {},
            "rates": {}
        },
        "visual_options":{
            "rates-title": "Percent of Submitted Samples Containing Fentanyl or Benzodiazepines in British Columbia by Year",
            "counts-title": "Number of Submitted Samples Containing Fentanyl or Benzodiazepines in British Columbia by Year",
            "table-title": "Presence of Fentanyl and Benzodiazepines in Submitted Samples by Year",
            "rates-y-axis-title": "Percent of Samples Containing Drug",
            "counts-y-axis-title": "Number of Samples Containing Drug",
            "table-rates-row": "Percent of Samples Pos. for replace_me",
            "table-counts-row": "Number of Samples Pos. for replace_me",
        },
        "additional_rows": {
            "Total Samples": []
        }
    }

    fent_benz_by_year["data"]["counts"]["x"] = [year for year in data_by_year.keys()]
    fent_benz_by_year["data"]["rates"]["x"] = [year for year in data_by_year.keys()]
    fent_benz_by_year["data"]["counts"]["Fentanyl"] = []
    fent_benz_by_year["data"]["rates"]["Fentanyl"] = []
    fent_benz_by_year["data"]["counts"]["Benzodiazepines"] = []
    fent_benz_by_year["data"]["rates"]["Benzodiazepines"] = []
    fent_benz_by_year["additional_rows"]["Total Samples"] = []
    for year, data in data_by_year.items():
        fentanyl_pos = data.loc[data["Fentanyl Strip"] == "Pos"]
        fent_benz_by_year["data"]["counts"]["Fentanyl"].append(len(fentanyl_pos))
        fent_benz_by_year["data"]["rates"]["Fentanyl"].append(round(((len(fentanyl_pos) / len(data)) * 100), 2))
        benzodiazepine_pos = data.loc[data["Benzo Strip"] == "Pos"]
        fent_benz_by_year["data"]["counts"]["Benzodiazepines"].append(len(benzodiazepine_pos))
        fent_benz_by_year["data"]["rates"]["Benzodiazepines"].append(round(((len(benzodiazepine_pos) / len(data)) * 100), 2))
        fent_benz_by_year["additional_rows"]["Total Samples"].append(len(data))

    # ---- Clean Data for Opioid Types by Year ----
    opioid_types_by_year = {
            "data_source": {
            "name": "British Columbia Centre for Substance Use (BCCSU)",
            "about": """
This data is collected from the British Columbia Centre on Substance Use (BCCSU) and is based on voluntary drug testing results.The data is collected from samples provided by individuals and organizations in British Columbia.The data is collected to help inform the public about the drug supply in British Columbia and to help inform harm reduction strategies.Please note that this data is not representative of the entire illicit drug supply in British Columbia,but rather provides a snapshot of the drug supply based on voluntary submissions.

For more information visit the BCCSU's Drug Sense website by clicking the button below:
            """,
            "link": "https://drugsense.bccsu.ubc.ca/",
            "last_updated": last_updated,
            "data_until": data_until
        },
        "data": {
            "counts": {},
            "rates": {}
        },
        "visual_options":{
            "rates-title": "Percent of Submitted Samples Containing Opioid Types by Year as per Voluntary Drug Testing Results",
            "counts-title": "Number of Submitted Samples Containing Opioid Types by Year as per Voluntary Drug Testing Results",
            "table-title": "Presence of Opioid Types in Submitted Samples by Year",
            "rates-y-axis-title": "Percent of Samples Containing Opioid Types",
            "counts-y-axis-title": "Number of Samples Containing Opioid Types",
            "table-rates-row": "Percent of Samples Pos. for replace_me",
            "table-counts-row": "Number of Samples Pos. for replace_me",
        },
        "additional_rows": {
            "Total Opioid Samples": [],
            "Total Samples": []
        }
    }

    opioid_types_by_year["data"]["counts"]["x"] = [year for year in data_by_year.keys()]
    opioid_types_by_year["data"]["rates"]["x"] = [year for year in data_by_year.keys()]
    opioid_types_by_year["additional_rows"]["Total Samples"] = []
    opioid_categories = ["Codeine", "Fentanyl", "Heroin", "Hydrocodone", "Hydromorphone", "Methadone", "Morphine", "Oxycodone", "Buprenorphine"]
    for category in opioid_categories:
        opioid_types_by_year["data"]["counts"][category] = []
        opioid_types_by_year["data"]["rates"][category] = []
    for year, data in data_by_year.items():
        # Add the total number of samples to the total samples row
        opioid_types_by_year["additional_rows"]["Total Samples"].append(len(data))
        # Filter the data for opioid samples
        opioid_data = data.loc[data["Category"] == "Opioid"].fillna("No Data")
        opioid_types_by_year["additional_rows"]["Total Opioid Samples"].append(len(opioid_data))
        for category in opioid_categories:
            type_data = opioid_data.loc[opioid_data["Spectrometer"].str.contains(category, case=False)]
            opioid_types_by_year["data"]["counts"][category].append(len(type_data))
            opioid_types_by_year["data"]["rates"][category].append(round((len(type_data)/len(opioid_data) * 100), 2))

    # ----- Geographic Map Setup -----
    geographic_map = {
        "visual_options":{
            "title": "Health Authorities in British Columbia",
            "click_line": "Click on a health authority to view detailed data for that area.",
        }
    }

    # ----- Clean Data for Drug Supply Geographically Pie Charts AND Regional Breakdowns -----
    geographical_drug_supply_pie = {
        "data_source": {
            "name": "British Columbia Centre for Substance Use (BCCSU)",
            "about": """
This data is collected from the British Columbia Centre on Substance Use (BCCSU) and is based on voluntary drug testing results.The data is collected from samples provided by individuals and organizations in British Columbia.The data is collected to help inform the public about the drug supply in British Columbia and to help inform harm reduction strategies.Please note that this data is not representative of the entire illicit drug supply in British Columbia,but rather provides a snapshot of the drug supply based on voluntary submissions.

For more information visit the BCCSU's Drug Sense website by clicking the button below:
            """,
            "link": "https://drugsense.bccsu.ubc.ca/",
            "last_updated": last_updated,
            "data_until": data_until
        },
        "data": {
            "counts": {}
        },
        "visual_options":{
            "visual-title": "Category of Voluntarily Submitted Drug Samples in the replace_with_health_authority Health Authority by Year",
            "table-title": "Category of Voluntarily Submitted Drug Samples in the replace_with_health_authority Health Authority by Year",
            "table-counts-row": "Samples Classified as replace_me",
            "table-rates-row": "Percent of Samples Classified as replace_me",
        },
        "tabular_data": {}
    }

    regional_drug_supply_breakdown = {
        "data_source": {
            "name": "British Columbia Centre for Substance Use (BCCSU)",
            "about": """
This data is collected from the British Columbia Centre on Substance Use (BCCSU) and is based on voluntary drug testing results.The data is collected from samples provided by individuals and organizations in British Columbia.The data is collected to help inform the public about the drug supply in British Columbia and to help inform harm reduction strategies.Please note that this data is not representative of the entire illicit drug supply in British Columbia,but rather provides a snapshot of the drug supply based on voluntary submissions.

For more information visit the BCCSU's Drug Sense website by clicking the button below:
            """,
            "link": "https://drugsense.bccsu.ubc.ca/",
            "last_updated": last_updated,
            "data_until": data_until
        },
        "data": {
            "counts": {},
            "rates": {}
        },
        "visual_options":{
            "counts-title": "Spectrometer Determined Makeup of replace_with_category Samples in the replace_with_health_authority Health Authority",
            "table-title": "Spectrometer Determined Makeup of replace_with_category Samples in the replace_with_health_authority Health Authority",
            "counts-y-axis-title": "Number of Samples Positive for Substance",
            "table-counts-row": "Spectrometer Positive for replace_me",
            "hover-type": "default",
            "hover-info": "name+y"
        },
        "tabular_data": {}
    }

    # Split the data into each unique health authority
    bc_drug_sense = bc_drug_sense.loc[bc_drug_sense["Health Authority"].notna()]
    health_authorities = bc_drug_sense["Health Authority"].unique()
    for health_authority in health_authorities:
        ha_title = health_authority.replace(" Health", "")
        # Filter the data for the health authority
        ha_data = bc_drug_sense.loc[bc_drug_sense["Health Authority"] == health_authority]
        # Create a dictionary for the health authority
        geographical_drug_supply_pie["data"]["counts"][ha_title] = {}
        regional_drug_supply_breakdown["data"]["counts"][ha_title] = {}
        regional_drug_supply_breakdown["data"]["rates"][ha_title] = {}
        geographical_drug_supply_pie["tabular_data"][ha_title] = {}
        for drug in drug_categories:
            geographical_drug_supply_pie["tabular_data"][ha_title][drug] = []
        geographical_drug_supply_pie["tabular_data"][ha_title]["Total Samples"] = []
        # Iterate over each year in the data
        for year in range_years:
            # Filter the data for the year
            year_data = ha_data.loc[ha_data["Visit Date"].str.contains(str(year))]
            # Create a dictionary for the year
            geographical_drug_supply_pie["data"]["counts"][ha_title][str(year)] = {}
            regional_drug_supply_breakdown["data"]["counts"][ha_title][str(year)] = {}
            regional_drug_supply_breakdown["data"]["rates"][ha_title][str(year)] = {}
            geographical_drug_supply_pie["tabular_data"][ha_title]["Total Samples"].append(len(year_data))
            # Iterate over each drug category in the data
            for drug in drug_categories:
                # Filter the data for the drug category
                drug_data = year_data.loc[year_data["Category"] == drug]
                # Add the count of samples to the dictionary
                geographical_drug_supply_pie["data"]["counts"][ha_title][str(year)][drug] = len(drug_data)
                # Add the count of samples to the tabular data
                geographical_drug_supply_pie["tabular_data"][ha_title][drug].append(len(drug_data))
                spectrometer_results = drug_data["Spectrometer"].dropna().str.cat(sep=", ").split(", ")
                # Count the unique results in the spectrometer results
                spectrometer_counts = {f"{result}_y": [spectrometer_results.count(result)] for result in set(spectrometer_results) if result != ""}
                # Add the spectrometer counts to the regional breakdown
                regional_drug_supply_breakdown["data"]["counts"][ha_title][str(year)][drug] = spectrometer_counts

    # ----- Clean Data for the Checked-Samples-by-Expected-Drug treemap -----
    # A reusable, granular fact set: one count per (Health Authority + Site, month, Category,
    # Expected Drug). The generic treemap renderer derives every dropdown + the time control from
    # these facts, so future BCCSU treemaps are just new manifest entries over the same data.
    checked_samples_by_expected_drug = {
        "data_source": {
            "name": "British Columbia Centre for Substance Use (BCCSU)",
            "about": """
This data is collected from the British Columbia Centre on Substance Use (BCCSU) and is based on voluntary drug testing results.The data is collected from samples provided by individuals and organizations in British Columbia.The data is collected to help inform the public about the drug supply in British Columbia and to help inform harm reduction strategies.Please note that this data is not representative of the entire illicit drug supply in British Columbia,but rather provides a snapshot of the drug supply based on voluntary submissions.

For more information visit the BCCSU's Drug Sense website by clicking the button below:
            """,
            "link": "https://drugsense.bccsu.ubc.ca/",
            "last_updated": last_updated,
            "data_until": data_until
        },
        "data": {"counts": {}},
    }
    # Parse "Visit Date" ("YYYY-MM-DD" in the scrape) down to a "YYYY-MM" month key.
    treemap_df = bc_drug_sense.dropna(
        subset=["Site", "Category", "Expected Drug", "Health Authority"]).copy()
    treemap_df["_month"] = pandas.to_datetime(
        treemap_df["Visit Date"], errors="coerce").dt.strftime("%Y-%m")
    treemap_df = treemap_df[treemap_df["_month"].notna()]
    for (ha, site), site_df in treemap_df.groupby(["Health Authority", "Site"]):
        # Ordered geo levels matching the manifest geo_levels=["Health Authority", "Site"]; to add a
        # Province stratifier later, prepend it here and to geo_levels. The client splits on "||".
        geo = f"{ha}||{site}"
        by_geo = checked_samples_by_expected_drug["data"]["counts"].setdefault(geo, {})
        grouped = site_df.groupby(["_month", "Category", "Expected Drug"]).size()
        for (month, category, drug), n in grouped.items():
            by_geo.setdefault(month, {}).setdefault(category, {})[drug] = int(n)

    # Compile all the data to a single dictionary for export
    bc_data = {
        "drug_death_heatmap": heatmap_data,
        "deaths_by_sex_line": death_by_sex_data,
        "drug_supply_by_year": drug_supply_by_year,
        "fent_benz_by_year": fent_benz_by_year,
        "opioid_types_by_year": opioid_types_by_year,
        "toxicity_deaths_per_drug_by_year": drug_toxicity_deaths_by_type,
        "drug_toxicity_deaths_by_age": drug_toxicity_deaths_by_age,
        "drug_supply_geographically": geographic_map,
        "geographical_drug_supply_pie": geographical_drug_supply_pie,
        "regional_drug_supply_breakdown": regional_drug_supply_breakdown,
        "checked_samples_by_expected_drug": checked_samples_by_expected_drug,
    }
    return bc_data

# Helper function to clean national data for each province
def v1_clean_national_data(province):
    # Pull up the data from the national health infobase file, it's a single df so we don't need to filter
    data = pull_data(["nationalHealthInfobase"])
    data = data["nationalHealthInfobase"]
    dataframe = data["dataframe"]
    last_updated = data["date_updated"]
    data_until = data["data_until"]

    # Grab the total opioid/stimulant deaths in the given province
    total_opioid = dataframe[(dataframe["Region"] == province) & (dataframe["Substance"] == "Opioids") & (dataframe["Specific_Measure"] == "Overall numbers") & (dataframe["Time_Period"] == "By year") & (dataframe["Source"] == "Deaths") & (dataframe["Unit"] == "Number")].infer_objects(copy=False).fillna(0)
    total_stimulant = dataframe[(dataframe["Region"] == province) & (dataframe["Substance"] == "Stimulants") & (dataframe["Specific_Measure"] == "Overall numbers") & (dataframe["Time_Period"] == "By year") & (dataframe["Source"] == "Deaths") & (dataframe["Unit"] == "Number")].infer_objects(copy=False).fillna(0)

    # Grab the population data for the given province
    population_data = pull_data(["nationalPopulationData"])
    population_data = filter_data(population_data, ["nationalPopulationData"])[0]["dataframe"]
    population_data = population_data.loc[population_data["GEO"] == province].set_index("REF_DATE")["VALUE"].to_dict()

    # ----- Opioid Deaths by Age group ----- TODO: Add stimulants to this
    opioid_deaths_by_age = {
        "data_source": {
            "name": "Health Infobase - Health data in Canada",
            "about": """
This data was collected from Canada's Health Infobase Opioid- and Stimulant-related Harms in Canada dataset, a report published quarterly on providing information on opioid and stimulant-related deaths and overdoses in Canada in collaboration with Chief Coroners, Chief Medical Examiners, Public Health agencies, and Emergency Medical Services from individual provinces and territories.

For more information visit the report directly by clicking the below:
            """,
            "link": "https://health-infobase.canada.ca/substance-related-harms/opioids-stimulants/",
            "last_updated": last_updated,
            "data_until": data_until
        },
        "data": {
            "counts": {},
            "percentages": {},
        },
        "visual_options":{
            "counts-title": f"Opioid Deaths in {province} by Age Group",
            "percentages-title": f"Percent of Total Opioid Deaths in {province} belonging to each Age Group",
            "table-title": f"Opioid Deaths in {province} by Age Group",
            "counts-y-axis-title": "Number of Opioid Deaths",
            "percentages-y-axis-title": "Percent of Total Opioid Deaths",
            "table-percentages-row": "Percent of Total Opioid Deaths for those aged replace_me",
            "table-counts-row": "Number of Opioid Deaths for those aged replace_me",
        }
    }
    percent_deaths_by_age = dataframe[(dataframe["Region"] == province) & (dataframe["Substance"] == "Opioids") & (dataframe["Specific_Measure"] == "Age group") & (dataframe["Time_Period"] == "By year") & (dataframe["Source"] == "Deaths")]
    age_groups = percent_deaths_by_age["Disaggregator"].unique()
    for age_group in age_groups:
        # Filter the data for the age group
        age_group_data = percent_deaths_by_age[percent_deaths_by_age["Disaggregator"] == age_group]
        # Get the year and percentage of deaths for the age group
        years = [str(year).replace(u"\xa0", "") for year in age_group_data["Year_Quarter"].unique()]
        opioid_deaths_by_age["data"]["percentages"]["x"] = years
        opioid_deaths_by_age["data"]["counts"]["x"] = years
        percentages = [float(value.replace(u"\xa0", "").replace("%", "")) if isinstance(value, str) else value for value in age_group_data["Value"].values]
        opioid_deaths_by_age["data"]["percentages"][f"{age_group}_y"] = percentages
        # Multiply the percentages by the total opioid deaths to get the counts
        for index, year in enumerate(years):
            opioid_deaths_by_age["data"]["counts"][f"{age_group}_y"] = [round((percentages[index] / 100) * int(list(total_opioid["Value"])[index])) for index in range(len(percentages))]

    # ----- Deaths by Drug Type -----
    deaths_by_drug_type = {
        "data_source": {
            "name": "Health Infobase - Health data in Canada",
            "about": """
This data was collected from Canada's Health Infobase Opioid- and Stimulant-related Harms in Canada dataset, a report published quarterly on providing information on opioid and stimulant-related deaths and overdoses in Canada in collaboration with Chief Coroners, Chief Medical Examiners, Public Health agencies, and Emergency Medical Services from individual provinces and territories.

For more information visit the report directly by clicking the below:
            """,
            "link": "https://health-infobase.canada.ca/substance-related-harms/opioids-stimulants/",
            "last_updated": last_updated,
            "data_until": data_until
        },
        "data": {
            "counts": {},
            "rates": {},
            "percentages": {},
        },
        "visual_options":{
            "counts-title": f"Deaths in {province} Attributed to Unregulated Drugs by Drug Type",
            "percentages-title": f"Percent of Total Unregulated Drug Deaths in {province} by Drug Type",
            "rates-title": f"Unregulated Drug Deaths per 100,000 Population in {province} by Drug Type",
            "table-title": f"Unregulated Drug Deaths in {province} by Drug Type",
            "counts-y-axis-title": "Number of Unregulated Drug Deaths",
            "percentages-y-axis-title": "Percent of Total Unregulated Drug Deaths",
            "rates-y-axis-title": "Unregulated Drug Deaths per 100,000 Population",
            "table-percentages-row": "Percent of Total Unregulated Drug Deaths Attributed to replace_me",
            "table-counts-row": "Unregulated Drug Deaths Attributed to replace_me",
            "table-rates-row": "Unregulated Drug Deaths Attributed to replace_me/100,000 Population",
            "hover-type": "x unified",
            "hover-info": "default"
        }
    }
    percent_opioid_deaths_by_drug = dataframe[(dataframe["Region"] == province) & (dataframe["Substance"] == "Opioids") & (dataframe["Specific_Measure"] == "Type of opioids") & (dataframe["Time_Period"] == "By year") & (dataframe["Source"] == "Deaths")]
    drug_types = percent_opioid_deaths_by_drug["Disaggregator"].unique()
    for drug_type in drug_types:
        # Filter the data for the drug type
        drug_type_data = percent_opioid_deaths_by_drug[percent_opioid_deaths_by_drug["Disaggregator"] == drug_type].fillna(0)
        # Get the year and percentage of deaths for the drug type
        years = [str(year).replace(u"\xa0", "") for year in drug_type_data["Year_Quarter"].unique()]
        deaths_by_drug_type["data"]["percentages"]["x"] = years
        deaths_by_drug_type["data"]["counts"]["x"] = years
        deaths_by_drug_type["data"]["rates"]["x"] = years
        percentages = [float(value.replace(u"\xa0", "").replace("%", "")) if isinstance(value, str) else value for value in drug_type_data["Value"].values]
        deaths_by_drug_type["data"]["percentages"][f"{drug_type}_y"] = percentages
        # Multiply the percentages by the total opioid deaths to get the counts
        for index, year in enumerate(years):
            deaths_by_drug_type["data"]["counts"][f"{drug_type}_y"] = [round((percentages[index] / 100) * int(list(total_opioid["Value"])[index])) for index in range(len(percentages))]
        # Calculate the rates of deaths per 100,000 population
        for index, year in enumerate(years):
            population = population_data[int(year.split(" ")[0])]
            deaths_by_drug_type["data"]["rates"][f"{drug_type}_y"] = [round((deaths_by_drug_type["data"]["counts"][f"{drug_type}_y"][index] / population) * 100000, 2) for index in range(len(percentages))]
    
    # Add the stimulant data too, unless the province doesn't have any
    percent_stimulant_deaths_by_drug = dataframe[(dataframe["Region"] == province) & (dataframe["Substance"] == "Stimulants") & (dataframe["Specific_Measure"] == "Type of stimulants") & (dataframe["Time_Period"] == "By year") & (dataframe["Source"] == "Deaths")].infer_objects(copy=False).fillna(0)
    if any(value != 0 for value in list(percent_stimulant_deaths_by_drug["Value"])): # Need this check becaus Alberta specifically doesn't have any stimulant data
        stimulant_drug_types = percent_stimulant_deaths_by_drug["Disaggregator"].unique()
        for drug_type in stimulant_drug_types:  
            # Filter the data for the drug type
            drug_type_data = percent_stimulant_deaths_by_drug[percent_stimulant_deaths_by_drug["Disaggregator"] == drug_type].fillna(0)
            # Get the year and percentage of deaths for the drug type
            years = [str(year).replace(u"\xa0", "") for year in drug_type_data["Year_Quarter"].unique()]
            if "x" not in deaths_by_drug_type["data"]["percentages"]:
                deaths_by_drug_type["data"]["percentages"]["x"] = years
                deaths_by_drug_type["data"]["counts"]["x"] = years
                deaths_by_drug_type["data"]["rates"]["x"] = years
            percentages = [float(value.replace(u"\xa0", "").replace("%", "")) if isinstance(value, str) else value for value in drug_type_data["Value"].values]
            deaths_by_drug_type["data"]["percentages"][f"{drug_type}_y"] = percentages
            # Multiply the percentages by the total stimulant deaths to get the counts
            for index, year in enumerate(years):
                deaths_by_drug_type["data"]["counts"][f"{drug_type}_y"] = [round((percentages[index] / 100) * int(list(total_stimulant["Value"])[index])) for index in range(len(percentages))]
            # Calculate the rates of deaths per 100,000 population
            for index, year in enumerate(years):
                population = population_data[int(year.split(" ")[0])]
                deaths_by_drug_type["data"]["rates"][f"{drug_type}_y"] = [round((deaths_by_drug_type["data"]["counts"][f"{drug_type}_y"][index] / population) * 100000, 2) for index in range(len(percentages))]
    
    # ----- Deaths by Sex -----
    deaths_by_sex = {
        "data_source": {
            "name": "Health Infobase - Health data in Canada",
            "about": """
This data was collected from Canada's Health Infobase Opioid- and Stimulant-related Harms in Canada dataset, a report published quarterly on providing information on opioid and stimulant-related deaths and overdoses in Canada in collaboration with Chief Coroners, Chief Medical Examiners, Public Health agencies, and Emergency Medical Services from individual provinces and territories.

For more information visit the report directly by clicking the below:
            """,
            "link": "https://health-infobase.canada.ca/substance-related-harms/opioids-stimulants/",
            "last_updated": last_updated,
            "data_until": data_until
        },
        "data": {
            "counts": {},
            "rates": {},
            "percentages": {},
        },
        "visual_options":{
            "counts-title": f"Unregulated Drug Toxicity Deaths in {province} by Sex",
            "rates-title": f"Unregulated Drug Toxicity Deaths in {province} per 100,000 Population by Sex",
            "percentages-title": f"Percent of Total Unregulated Drug Toxicity Deaths in {province} by Sex",
            "table-title": f"Unregulated Drug Toxicity Deaths in {province} by Sex",
            "counts-y-axis-title": "Number of Unregulated Drug Toxicity Deaths",
            "rates-y-axis-title": "Unregulated Drug Deaths/100,000 Population",
            "percentages-y-axis-title": "Percent of Total Unregulated Drug Toxicity Deaths",
            "table-percentages-row": "Percent of Total Unregulated Drug Toxicity Deaths that were replace_me Deaths",
            "table-rates-row": "Unregulated Drug Toxicity Deaths/100,000 Population that were replace_me Deaths",
            "table-counts-row": "Unregulated Drug Toxicity Deaths that were replace_me Deaths",
        }
    }

    percentages = dataframe[(dataframe["Region"] == province) & (dataframe["Substance"] == "Opioids") & (dataframe["Specific_Measure"] == "Sex") & (dataframe["Time_Period"] == "By year") & (dataframe["Source"] == "Deaths") & (dataframe["Unit"] == "Percent")].infer_objects(copy=False).fillna(0)
    rates = dataframe[(dataframe["Region"] == province) & (dataframe["Substance"] == "Opioids") & (dataframe["Specific_Measure"] == "Sex") & (dataframe["Time_Period"] == "By year") & (dataframe["Source"] == "Deaths") & (dataframe["Unit"] == "Crude rate")].infer_objects(copy=False).fillna(0)
    sexes = percentages["Disaggregator"].unique()
    years = list(rates["Year_Quarter"].unique())
    deaths_by_sex["data"]["counts"]["x"] = years
    deaths_by_sex["data"]["percentages"]["x"] = years
    deaths_by_sex["data"]["rates"]["x"] = years
    for sex in sexes:
        # Filter the data for the current sex
        sex_percents = percentages[percentages["Disaggregator"] == sex]
        sex_rates = rates[rates["Disaggregator"] == sex]
        deaths_by_sex["data"]["percentages"][f"{sex} Opioid_y"] = sex_percents["Value"].tolist()
        deaths_by_sex["data"]["rates"][f"{sex} Opioid_y"]  = sex_rates["Value"].tolist()
        # Multiply the percentages by the total opioid deaths to get the counts
        for index, year in enumerate(years):
            total_deaths = int(list(total_opioid["Value"])[index])
            deaths_by_sex["data"]["counts"][f"{sex} Opioid_y"] = [round((int(deaths_by_sex["data"]["percentages"][f"{sex} Opioid_y"][index]) / 100) * total_deaths) for index in range(len(years))]

    percentages = dataframe[(dataframe["Region"] == province) & (dataframe["Substance"] == "Stimulants") & (dataframe["Specific_Measure"] == "Sex") & (dataframe["Time_Period"] == "By year") & (dataframe["Source"] == "Deaths") & (dataframe["Unit"] == "Percent")].infer_objects(copy=False).fillna(0)
    rates = dataframe[(dataframe["Region"] == province) & (dataframe["Substance"] == "Stimulants") & (dataframe["Specific_Measure"] == "Sex") & (dataframe["Time_Period"] == "By year") & (dataframe["Source"] == "Deaths") & (dataframe["Unit"] == "Crude rate")].infer_objects(copy=False).fillna(0)
    stimulant_years = list(rates["Year_Quarter"].unique())
    more_opioid_than_stimulant = len(years) - len(stimulant_years)
    if more_opioid_than_stimulant < 0:
        more_opioid_than_stimulant = 0
    if any(value != 0 for value in list(percentages["Value"])): # Need this check because Alberta specifically doesn't have any stimulant data
        sexes = percentages["Disaggregator"].unique()
        for sex in sexes:
            # Filter the data for the current sex
            sex_percents = percentages[percentages["Disaggregator"] == sex]
            deaths_by_sex["data"]["percentages"][f"{sex} Stimulant_y"] = ["0"] * more_opioid_than_stimulant + sex_percents["Value"].tolist()
            sex_rates = rates[rates["Disaggregator"] == sex]
            deaths_by_sex["data"]["rates"][f"{sex} Stimulant_y"]  = ["0"] * more_opioid_than_stimulant +sex_rates["Value"].tolist()
            # Multiply the percentages by the total opioid deaths to get the counts
            for index, year in enumerate(years):
                total_deaths = int(list(total_opioid["Value"])[index])
                deaths_by_sex["data"]["counts"][f"{sex} Stimulant_y"] = [round((int(deaths_by_sex["data"]["percentages"][f"{sex} Stimulant_y"][index]) / 100) * total_deaths) for index in range(len(years))]

    # ----- Deaths by Manner of Death -----
    deaths_by_manner = {
        "data_source": {
            "name": "Health Infobase - Health data in Canada",
            "about": """
This data was collected from Canada's Health Infobase Opioid- and Stimulant-related Harms in Canada dataset, a report published quarterly on providing information on opioid and stimulant-related deaths and overdoses in Canada in collaboration with Chief Coroners, Chief Medical Examiners, Public Health agencies, and Emergency Medical Services from individual provinces and territories.

For more information visit the report directly by clicking the below:
            """,
            "link": "https://health-infobase.canada.ca/substance-related-harms/opioids-stimulants/",
            "last_updated": last_updated,
            "data_until": data_until
        },
        "data": {
            "counts": {},
            "rates": {},
            "percentages": {},
        },
        "visual_options":{
            "counts-title": f"Unregulated Drug Toxicity Deaths in {province} by Manner of Death",
            "rates-title": f"Unregulated Drug Toxicity Deaths in {province} per 100,000 Population by Manner of Death",
            "percentages-title": f"Percent of Total Unregulated Drug Toxicity Deaths in {province} by Manner of Death",
            "table-title": f"Unregulated Drug Toxicity Deaths in {province} by Manner of Death",
            "counts-y-axis-title": "Number of Unregulated Drug Toxicity Deaths",
            "rates-y-axis-title": "Unregulated Drug Deaths/100,000 Population",
            "percentages-y-axis-title": "Percent of Total Unregulated Drug Toxicity Deaths",
            "table-percentages-row": "Percent of Total Unregulated Drug Toxicity Deaths that were replace_me",
            "table-rates-row": "Unregulated Drug Toxicity Deaths/100,000 Population that were replace_me",
            "table-counts-row": "Unregulated Drug Toxicity Deaths that were replace_me",
        }
    }

    opioid_percentages = dataframe[(dataframe["Region"] == province) & (dataframe["Substance"] == "Opioids") & (dataframe["Specific_Measure"] == "Manner of death") & (dataframe["Time_Period"] == "By year") & (dataframe["Source"] == "Deaths") & (dataframe["Unit"] == "Percent")].infer_objects(copy=False).fillna(0)
    stimulant_percentages = dataframe[(dataframe["Region"] == province) & (dataframe["Substance"] == "Stimulants") & (dataframe["Specific_Measure"] == "Manner of death") & (dataframe["Time_Period"] == "By year") & (dataframe["Source"] == "Deaths") & (dataframe["Unit"] == "Percent")].infer_objects(copy=False).fillna(0)
    types_of_deaths = [f"{type} Opioid Deaths" for type in opioid_percentages["Disaggregator"].unique()] + [f"{type} Stimulant Deaths" for type in stimulant_percentages["Disaggregator"].unique()]
    opioid_years = list(opioid_percentages["Year_Quarter"].unique())
    stimulant_years = list(stimulant_percentages["Year_Quarter"].unique())
    more_opioid_than_stim = (len(opioid_years)) - (len(stimulant_years))
    deaths_by_manner["data"]["counts"]["x"] = years
    deaths_by_manner["data"]["percentages"]["x"] = years
    deaths_by_manner["data"]["rates"]["x"] = years
    # Ignore Stimulants if we're in alberta
    if all(value == 0 for value in list(stimulant_percentages["Value"])): # Need this check because Alberta specifically doesn't have any stimulant data
        types_of_deaths = [type for type in types_of_deaths if "stimulant" not in type.lower()]
    for type in types_of_deaths:
        if "opioid" in type.lower():
            # Filter the data for the current manner of death
            manner_percents = opioid_percentages[opioid_percentages["Disaggregator"] == type.replace(" Opioid Deaths", "")]
            deaths_by_manner["data"]["percentages"][type] = manner_percents["Value"].tolist()
            # if there are less years than opioid years, then add prepending 0s to data
            if more_opioid_than_stim < 0:
                deaths_by_manner["data"]["percentages"][type] = ["0"] * (more_opioid_than_stim * -1) + deaths_by_manner["data"]["percentages"][type]
        if "stimulant" in type.lower():
            manner_percents = stimulant_percentages[stimulant_percentages["Disaggregator"] == type.replace(" Stimulant Deaths", "")]
            deaths_by_manner["data"]["percentages"][type] = manner_percents["Value"].tolist()
            # if there are less years than opioid years, then add prepending 0s to data
            if more_opioid_than_stim > 0:
                deaths_by_manner["data"]["percentages"][type] = ["0"] * more_opioid_than_stim + deaths_by_manner["data"]["percentages"][type]
        # Multiply the percentages by the total opioid deaths to get the counts
        for index, year in enumerate(years):
            total_deaths = int(list(total_opioid["Value"])[index])
            deaths_by_manner["data"]["counts"][type] = [round((int(deaths_by_manner["data"]["percentages"][type][index]) / 100) * total_deaths) for index in range(len(years))]
            population = population_data[int(year.split(" ")[0])]
            deaths_by_manner["data"]["rates"][type] = [round((deaths_by_manner["data"]["counts"][type][index] / population) * 100000, 2) for index in range(len(years))]

    return opioid_deaths_by_age, deaths_by_drug_type, deaths_by_sex, deaths_by_manner

def v1_AB_export_clean():
    # ----- Cleaned data from national health infobase -----
    opioid_deaths_by_age, deaths_by_drug_type, deaths_by_sex, deaths_by_manner = v1_clean_national_data("Alberta")

    # ----- Aggregate all data for export -----
    ab_data = {
        "opioid_deaths_by_age": opioid_deaths_by_age,
        "deaths_by_drug_type": deaths_by_drug_type,
        "deaths_by_sex": deaths_by_sex,
        "deaths_by_manner": deaths_by_manner
    }
    return ab_data

def v1_MB_export_clean():
    # ----- Cleaned data from national health infobase -----
    deaths_by_age, deaths_by_drug_type, deaths_by_sex, deaths_by_manner = v1_clean_national_data("Manitoba")

    # ----- Aggregate all data for export -----
    mb_data = {
        "deaths_by_age": deaths_by_age,
        "deaths_by_drug_type": deaths_by_drug_type,
        "deaths_by_sex": deaths_by_sex,
        "deaths_by_manner": deaths_by_manner
    }
    return mb_data

def v1_SK_export_clean():
    # ----- Cleaned data from national health infobase -----
    deaths_by_age, deaths_by_drug_type, deaths_by_sex, deaths_by_manner = v1_clean_national_data("Saskatchewan")

    # ----- Pull and Filter the SK Data -----
    to_filter = pull_data(["skPubCentre"])
    sk_pub_centre = filter_data(to_filter, ["Confirmed&SuspectedDrugToxicityDeathsbyMannerofDeath","BreakdownofOpioidDrugsIdentifiedinConfirmedDrugToxicityDeathsbyMannerofDeath", "ConfirmedDrugToxicityDeathsbyPlaceofDeath"])
    
    # ----- Pull the total CONFIRMED deaths to use in other calculations -----
    data = sk_pub_centre[0]["dataframe"]
    # Drop the total and suspected rows
    data = data[(data["Year"] != "Total") & (data["Year"] != "Suspected")]
    # Convert all columns except MannerOfDeath to numeric, forcing errors to NaN then
    data = data.replace("--", 0)
    for col in data.columns:
        if col != "Year":
            data[col] = pandas.to_numeric(data[col], errors="coerce").fillna(0).astype(int)
    # sum each column
    total_dict = data.sum(numeric_only=True).to_dict()
    total_list = data.sum(numeric_only=True).to_list()

    # ----- Deaths by Place of Death -----
    data = sk_pub_centre[2]["dataframe"]
    data = data.replace("-", 0)
    last_updated = sk_pub_centre[2]["date_updated"]
    data_until = sk_pub_centre[2]["data_until"]
    sk_report_deaths_by_place = { 
        "data_source": {
            "name": "Saskatchewan Coroners Service",
            "about": """
This data has been collected by the Saskatchewan Coroners Service (SKCS), and is based on toxicology reports from individuals who have died in Saskatchewan where the cause of death was confirmed, or suspected to be,drug toxicity.The data is updated monthly by the SKCS

For more information,visit the SKCS website to view the PDF report by clicking the button below:
            """,
            "link": "https://publications.saskatchewan.ca/#/products/90505",
            "last_updated": last_updated,
            "data_until": data_until
        },
        "data": {
            "counts": {},
        },
        "visual_options": {
            "heatmap-title": "Unregulated Drug Deaths in Saskatchewan by Health Authority",
            "table-title": "Unregulated Drug Deaths in replace_with_health_authority Health Authority",
            "table-row-title": "replace_location",
        }
    }
    years = data.columns[1:].to_list()
    #Load the key
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static/js/SK_HA_key.json")
    with open(filepath, "r") as file:
        ha_key = json.load(file)
    # Instantiate a dict of health authorities with empty lists to hold deaths in each
    health_authorities = {ha: [0] * len(years) for ha in ha_key.keys()}
    def get_key_from_value(dict, value):
        for key, values in dict.items():
            if value in values:
                return key
        return None
    for index, row in data.iterrows():
        location = row["Location"]
        health_authority = get_key_from_value(ha_key, location)
        if health_authority:
            for index, year in enumerate(years):
                health_authorities[health_authority][index] += int(row[year])
        elif location.lower() == "total":
            #Add this to a total row eventually
            health_authorities["Saskatchewan"] = [int(row[year]) for year in years]
        else:
            if "Unknown" not in health_authorities:
                health_authorities["Unknown"] = [0] * len(years)
            for index, year in enumerate(years):
                health_authorities["Unknown"][index] += int(row[year])
    for health_authority, counts in health_authorities.items():
        if health_authority not in sk_report_deaths_by_place["data"]["counts"].keys():
            sk_report_deaths_by_place["data"]["counts"][f"{health_authority}"] = {}
        sk_report_deaths_by_place["data"]["counts"][f"{health_authority}"]["y"] = counts
        sk_report_deaths_by_place["data"]["counts"][f"{health_authority}"]["x"] = years

    # ----- Deaths by Opioid Type -----
    data = sk_pub_centre[1]["dataframe"]
    last_updated = sk_pub_centre[1]["date_updated"]
    data_until = sk_pub_centre[1]["data_until"]
    sk_report_deaths_by_type = {
        "data_source": {
            "name": "Saskatchewan Coroners Service",
            "about": """
This data has been collected by the Saskatchewan Coroners Service (SKCS), and is based on toxicology reports from individuals who have died in Saskatchewan where the cause of death was confirmed, or suspected to be,drug toxicity.The data is updated monthly by the SKCS

For more information,visit the SKCS website to view the PDF report by clicking the button below:
            """,
            "link": "https://publications.saskatchewan.ca/#/products/90505",
            "last_updated": last_updated,
            "data_until": data_until
        },
        "data": {
            "counts": {},
            "rates": {},
            "percentages": {},
        },
        "visual_options":{
            "counts-title": f"Deaths in Saskatchewan Attributed to Unregulated Drugs by Drug Type",
            "percentages-title": f"Percent of Total Unregulated Drug Deaths in Saskatchewan by Drug Type",
            "rates-title": f"Unregulated Drug Deaths per 100,000 Population in Saskatchewan by Drug Type",
            "table-title": f"Unregulated Drug Deaths in Saskatchewan by Drug Type",
            "counts-y-axis-title": "Number of Unregulated Drug Deaths",
            "percentages-y-axis-title": "Percent of Total Unregulated Drug Deaths",
            "rates-y-axis-title": "Unregulated Drug Deaths per 100,000 Population",
            "table-percentages-row": "Percent of Total Unregulated Drug Deaths Attributed to replace_me",
            "table-counts-row": "Unregulated Drug Deaths Attributed to replace_me",
            "table-rates-row": "Unregulated Drug Deaths Attributed to replace_me/100,000 Population",
            "hover-type": "x unified",
            "hover-info": "default"
        }
    }
    # Because of the way these show up in the PDF we have to do a little extra cleaning
    # Remove the manner of death column
    data = data.drop(columns=["MannerOfDeath"])
    drug_types = data.columns[1:].to_list()
    for index, drug in enumerate(drug_types): 
        if drug == "FuranylFentanyl":
            drug_types[index] = "Furanyl Fentanyl"
        elif drug == "FuranylUF-17":
            drug_types[index] = "Furanyl UF-17"
        elif drug == "Opioid(Unknown)":
            drug_types[index] = "Opioid (Unknown)"
    # Reset the columns to their new names
    data.columns = ["Year"] + drug_types
    # Replace all the "--" with 0 values
    data = data.replace("--", 0)
    for col in data.columns:
        if col != "Year":
            data[col] = pandas.to_numeric(data[col], errors="coerce").fillna(0).astype(int)
    data = data.groupby("Year", as_index=False).sum(numeric_only=True)
    
    # Clean the data and separate it into dicts for the generation of visuals
    years = data["Year"].tolist()
    sk_report_deaths_by_type["data"]["counts"]["x"] = years
    sk_report_deaths_by_type["data"]["percentages"]["x"] = years
    population_data = pull_data(["nationalPopulationData"])
    population_data = filter_data(population_data, ["nationalPopulationData"])[0]["dataframe"]
    population_data = population_data.loc[population_data["GEO"] == "Saskatchewan"].set_index("REF_DATE")["VALUE"].to_dict()
    pop_years = list(population_data.keys())
    sk_report_deaths_by_type["data"]["rates"]["x"] = [year for year in pop_years if f"{year}" in years]
    for drug in drug_types:
        counts = data[drug].tolist()
        sk_report_deaths_by_type["data"]["counts"][f"{drug}_y"] = counts
        percentages = []
        for index, count in enumerate(counts):
            percentages.append(round((count / total_list[index]) * 100, 2) if total_list[index] != 0 else 0)
        sk_report_deaths_by_type["data"]["percentages"][f"{drug}_y"] = percentages
        rates = [round((count / population_data[int(year)]) * 100000, 2) if population_data[int(year)] != 0 else 0 for index, (year, count) in enumerate(zip(sk_report_deaths_by_type["data"]["rates"]["x"], counts))]
        sk_report_deaths_by_type["data"]["rates"][f"{drug}_y"] = rates

    # ----- Aggregate all data for export -----
    sk_data = {
        "opioid_deaths_by_age": deaths_by_age,
        "deaths_by_opioid_type": sk_report_deaths_by_type,
        "deaths_by_sex": deaths_by_sex,
        "deaths_by_manner": deaths_by_manner,
        "drug_death_heatmap": sk_report_deaths_by_place
    }
    return sk_data

def v1_NB_export_clean():
        # ----- Cleaned data from national health infobase -----
    opioid_deaths_by_age, deaths_by_drug_type, deaths_by_sex, deaths_by_manner = v1_clean_national_data("New Brunswick")

    # ----- Aggregate all data for export -----
    nb_data = {
        "opioid_deaths_by_age": opioid_deaths_by_age,
        "deaths_by_drug_type": deaths_by_drug_type,
        "deaths_by_sex": deaths_by_sex,
        "deaths_by_manner": deaths_by_manner
    }
    return nb_data



###########################################################################################
#                          DB persistence (the data layer)                                #
# Reuses the exact same cleaned dicts the v1_*_export_clean() functions already produce and #
# writes their facts as normalized rows: DataSources (about + scrape dates), DataPoints     #
# (facts), and VisualQuery (the predicates that select a visual's facts). The Visuals rows  #
# that *describe* each visual (shape/metric/dimensions/key encoding/menu config) are        #
# authored separately from JSON manifests by data_viz/visual_definitions.py                 #
# (`flask define-visuals`); this layer reads each Visuals row to learn how to map its       #
# cleaned block into facts -- there is no hard-coded VISUAL_SPECS / VISUAL_MENU registry.   #
###########################################################################################

# --------------------------------------------------------------------------------------- #
# Write-side constants + helpers for mapping cleaned blocks into normalized facts.
# --------------------------------------------------------------------------------------- #

# URL-friendly province -> the display/Region name used as the geo for province-level facts
PROVINCE_DISPLAY = {
    "british-columbia": "British Columbia",
    "alberta": "Alberta",
    "saskatchewan": "Saskatchewan",
    "manitoba": "Manitoba",
    "new-brunswick": "New Brunswick",
}

TIME_FRAME_TYPE = "year"
ADDITIONAL_DIM_TYPE = "additional_label"   # tags a table-only total row in dimension2

# substance token in the legacy series keys -> the clean dimension value
SUBSTANCE_FROM_KEY = {"Opioid": "opioids", "Stimulant": "stimulants"}

_MANNER_SUFFIX = " Deaths"


def additional_metric(label):
    """Stable metric name for a table-only total row, derived from its display label."""
    return "total_" + label.replace("Total ", "").strip().replace(" ", "_").lower()


def encode_series_key(visual, key, substance_map=None):
    """Legacy series key -> (dimension_value, dimension2_value) for the substance + disaggregator dims.
    dimension (slot 1) holds the substance; dimension2 (slot 2) holds the key's disaggregator.
    `visual` is the Visuals row -- its key_kind / substance columns drive the encoding."""
    kind = visual.key_kind
    if kind == "constant":
        return None, None
    if kind in ("suffix_y", "plain"):
        disaggregator = key[:-2] if (kind == "suffix_y" and key.endswith("_y")) else key
        return _resolve_substance(visual, disaggregator, substance_map), disaggregator
    if kind == "sex_substance":
        base = key[:-2] if key.endswith("_y") else key
        sex, token = base.rsplit(" ", 1)
        return SUBSTANCE_FROM_KEY.get(token, token), sex
    if kind == "manner_substance":
        base = key[:-len(_MANNER_SUFFIX)] if key.endswith(_MANNER_SUFFIX) else key
        manner, token = base.rsplit(" ", 1)
        return SUBSTANCE_FROM_KEY.get(token, token), manner
    raise ValueError(f"Unknown key kind: {kind}")


def _resolve_substance(visual, disaggregator, substance_map):
    mode = visual.substance
    if mode == "opioids":
        return "opioids"
    if mode == "from_key":
        # Substance is carried by the series key itself: parse it from the disaggregator the same way
        # the sex_substance/manner_substance kinds do, falling back to the raw value if unmapped.
        return SUBSTANCE_FROM_KEY.get(disaggregator, disaggregator)
    if mode == "lookup" and substance_map:
        return substance_map.get(disaggregator)
    return None


# URL-friendly province key -> its cleaning function
V1_PROVINCES = {
    "british-columbia": v1_BC_export_clean,
    "alberta": v1_AB_export_clean,
    "saskatchewan": v1_SK_export_clean,
    "manitoba": v1_MB_export_clean,
    "new-brunswick": v1_NB_export_clean,
}


def _national_substance_map():
    """{drug-type disaggregator: 'opioids'|'stimulants'} so national deaths_by_drug_type rows can
    carry a substance dimension (the cleaned dict merges the two without tagging them)."""
    try:
        dataframe = pull_data(["nationalHealthInfobase"])["nationalHealthInfobase"]["dataframe"]
    except Exception:
        return {}
    mapping = {}
    for _, row in dataframe[dataframe["Specific_Measure"] == "Type of opioids"].iterrows():
        mapping[str(row["Disaggregator"])] = "opioids"
    for _, row in dataframe[dataframe["Specific_Measure"] == "Type of stimulants"].iterrows():
        mapping.setdefault(str(row["Disaggregator"]), "stimulants")
    return mapping


def _split_value(value):
    """Return (numeric_or_None, text_or_None). Strings round-trip verbatim via the text column;
    numbers go in the float column (queryable)."""
    if isinstance(value, str):
        try:
            return float(value), value
        except ValueError:
            return None, value
    if value is None:
        return None, None
    return float(value), None


def _primary_x(block):
    """The x (years) list that additional/table rows are aligned to."""
    data = block.get("data", {})
    for dtype in ("counts", "rates", "percentages"):
        if dtype in data and "x" in data[dtype]:
            return data[dtype]["x"]
    return []


class FactWriter:
    """Collects normalized facts during a regeneration run, then drops & rewrites ONLY the rows the
    run reproduces -- the ``(data_source_id, geo)`` territory it emitted + the touched visuals'
    predicates -- in a single transaction, so untouched sources keep their rows.

    New-style cleaners emit through :meth:`visual` / :class:`VisualWriter`; the legacy block engine
    emits through :meth:`point` / :meth:`predicate`. Both share this buffer + scoped :meth:`finish`."""

    def __init__(self, db, models):
        self.db = db
        self.DataSources, self.DataPoints, self.Visuals, self.VisualQuery = models
        self._sources = {}        # name -> DataSources row (upserted immediately for its id)
        self._points = {}         # natural-key -> buffered DataPoints (dedups within the run)
        self._preds = {}          # (visual_id, type, value) -> buffered VisualQuery kwargs (dedup)
        self._territory = set()   # (data_source_id, geo) pairs this run reproduces -> delete scope
        self._visual_ids = set()  # visuals whose predicates this run reproduces -> delete scope

    def upsert_source(self, data_source):
        """Fetch/create the DataSources row by name, refresh its about/scrape-date strings, return id."""
        name = data_source["name"]
        source = self._sources.get(name)
        if source is None:
            source = self.DataSources.query.filter_by(name=name).first() or self.DataSources(name=name)
            self.db.session.add(source)
            self._sources[name] = source
        source.link = data_source.get("link", source.link)
        source.about = data_source.get("about")
        source.last_updated_str = data_source.get("last_updated")
        source.data_until_str = data_source.get("data_until")
        self.db.session.flush()   # need source.id
        return source.id

    def point(self, source_id, geo_type, geo, time_frame, metric, data_type,
              dim_type=None, dim_val=None, dim2_type=None, dim2_val=None, value=None,
              time_frame_type=TIME_FRAME_TYPE):
        """Buffer one DataPoints row (dedup by natural key) and record its (source, geo) territory."""
        key = (source_id, geo_type, geo, str(time_frame), metric, data_type,
               dim_type, dim_val, dim2_type, dim2_val)
        if key in self._points:
            return
        num, text = _split_value(value)
        self._points[key] = self.DataPoints(
            data_source_id=source_id, geo_type=geo_type, geo=geo,
            time_frame_type=time_frame_type, time_frame=str(time_frame),
            data_metric=metric, data_type=data_type,
            dimension_type=dim_type, dimension_value=dim_val,
            dimension2_type=dim2_type, dimension2_value=dim2_val,
            data_value=num, data_value_text=text,
        )
        if source_id is not None:
            self._territory.add((source_id, geo))

    def predicate(self, visual_id, filter_type, filter_value):
        """Buffer a VisualQuery predicate (dedup) and mark the visual for a scoped predicate refresh."""
        self._visual_ids.add(visual_id)
        self._preds[(visual_id, filter_type, str(filter_value))] = dict(
            for_visual_id=visual_id, filter_type=filter_type, filter_value=str(filter_value))

    def visual(self, province, visual_id):
        """A VisualWriter bound to this visual's Visuals row, or None (with a warning) if undefined."""
        row = self.Visuals.query.filter_by(province=province, name=visual_id).first()
        if row is None:
            print(f"  ! No definition for {province}/{visual_id} -- "
                  f"run `flask define-visuals` first. Skipping its data.")
            return None
        return VisualWriter(self, row)

    def finish(self):
        """One transaction: drop only the reproduced (source, geo) territory + touched predicates,
        then insert the buffered rows. Other sources/provinces are left untouched."""
        for source_id, geo in self._territory:
            self.DataPoints.query.filter_by(data_source_id=source_id, geo=geo).delete()
        for visual_id in self._visual_ids:
            self.VisualQuery.query.filter_by(for_visual_id=visual_id).delete()
        self.db.session.flush()
        for point in self._points.values():
            self.db.session.add(point)
        for kwargs in self._preds.values():
            self.db.session.add(self.VisualQuery(**kwargs))
        self.db.session.commit()


class VisualWriter:
    """Bound to one Visuals row: cleaning passes VALUES (geo/time/dim values/count); the metric and
    dimension TYPES come from the row, so the manifest stays the single source of truth."""

    def __init__(self, writer, visual):
        self.writer = writer
        self.visual = visual
        self.source_id = visual.data_source_id

    def use_source(self, data_source):
        """Refresh this run's DataSources metadata from the freshly scraped block."""
        self.source_id = self.writer.upsert_source(data_source)
        return self

    def _dim_type(self, dimension):
        # regional/treemap author dimension_type in the manifest; the flat/geo substance slot is
        # untyped there, so default it to "substance" when a substance value is supplied.
        return self.visual.dimension_type or ("substance" if dimension is not None else None)

    def fact(self, geo, time_frame, value, *, data_type="counts",
             dimension=None, dimension2=None, time_frame_type=None):
        v = self.visual
        self.writer.point(self.source_id, v.geo_type, geo, time_frame, v.metric, data_type,
                          self._dim_type(dimension), dimension, v.dimension2_type, dimension2, value,
                          time_frame_type=(time_frame_type or TIME_FRAME_TYPE))
        if v.geo_type == "province":
            self.writer.predicate(v.id, "geo", geo)   # province-shared facts scoped to this geo

    def additional(self, geo, time_frame, label, value, *, time_frame_type=None):
        """A table-only total row: one additional_rows fact + its additional_metric predicate."""
        metric = additional_metric(label)
        self.writer.point(self.source_id, self.visual.geo_type, geo, time_frame, metric,
                          "additional_rows", ADDITIONAL_DIM_TYPE, label, None, None, value,
                          time_frame_type=(time_frame_type or TIME_FRAME_TYPE))
        self.writer.predicate(self.visual.id, "additional_metric", metric)


# URL-friendly target key -> new-style cleaner that emits straight to the writer: builder(writer, key)
V1_DIRECT = {
    "canada": v1_drugchecking_export_clean,
}


def export_data_to_db(only=None, data=None):
    """Regenerate V1 facts into DataPoints + VisualQuery.

    `only`: iterable of target/province keys (e.g. ["canada"]) to regenerate; None = all targets.
    Only the rows the selected run reproduces -- its (data_source_id, geo) territory + the touched
    visuals' predicates -- are dropped and rewritten, in one transaction, so untouched sources keep
    their rows and a target whose scrape is missing is simply skipped.

    Visual *definitions* (the Visuals rows) are authored separately by `flask define-visuals`; this
    layer reads each row to learn how to map cleaned data into facts. `data` may be injected
    ({province: {visual_id: block}}) to persist already-cleaned legacy blocks without re-scraping.
    """
    from data_viz.database import db
    from data_viz.database.models import DataSources, DataPoints, Visuals, VisualQuery

    writer = FactWriter(db, (DataSources, DataPoints, Visuals, VisualQuery))
    targets = set(only) if only else None
    substance_map = _national_substance_map()

    # New-style cleaners emit straight into the writer's buffer.
    for province, builder in V1_DIRECT.items():
        if targets and province not in targets:
            continue
        try:
            builder(writer, province)
        except FileNotFoundError as exc:
            # Only a *missing scrape* is skipped (the province keeps its existing rows). Any other
            # error (e.g. a cleaner referencing a column the source dropped) is a real defect and is
            # left to propagate rather than silently dropping the province's data.
            logger.warning("Skipping %s: missing scrape (%s)", province, exc)

    # Legacy block-dict cleaners -> the same writer via _persist_legacy_blocks (unchanged shapes).
    if data is None:
        data = {}
        for province, builder in V1_PROVINCES.items():
            if targets and province not in targets:
                continue
            try:
                data[province] = builder()
            except FileNotFoundError as exc:
                logger.warning("Skipping %s: missing scrape (%s)", province, exc)
    for province, visuals in data.items():
        if targets and province not in targets:
            continue
        _persist_legacy_blocks(writer, province, visuals, substance_map)

    writer.finish()


# Per-shape required structure for a legacy block dict: the nested key path each branch of
# _persist_legacy_blocks dereferences. Validated up front so a shape<->structure mismatch fails with
# a message naming the visual, instead of a KeyError/TypeError deep inside persistence.
_BLOCK_REQUIRED_KEYS = {
    "flat_series": [["data"]],
    "geo_series": [["data"]],
    "pie_nested": [["data", "counts"]],
    "regional": [["data", "counts"]],
    "category_treemap": [["data", "counts"]],
    "map_none": [],
}


def _validate_block(shape, block, province, visual_id):
    """Assert `block` carries the nested keys `shape` will dereference; raise ValueError naming the
    visual otherwise. Unknown shapes are left to the branch dispatch below (no-op here)."""
    for path in _BLOCK_REQUIRED_KEYS.get(shape, []):
        node = block
        for key in path:
            if not isinstance(node, dict) or key not in node:
                raise ValueError(
                    f"{province}/{visual_id} (shape={shape}) is missing required block key "
                    f"{'->'.join(path)}")
            node = node[key]


def _persist_legacy_blocks(writer, province, visuals, substance_map):
    """Map each legacy cleaned block dict into facts via the writer -- the original per-shape
    unpacking, now emitting through writer.point()/predicate(). Deleted once every legacy cleaner
    has migrated to the VisualWriter.fact() API."""
    for visual_id, block in visuals.items():
        vw = writer.visual(province, visual_id)
        if vw is None:
            continue
        row = vw.visual
        shape = row.data_shape
        _validate_block(shape, block, province, visual_id)
        if "data_source" in block:
            vw.use_source(block["data_source"])
        source_id = vw.source_id
        geo_type = row.geo_type

        if shape == "map_none":
            continue   # no facts; the read path renders it from visual_options only

        if shape == "flat_series":
            geo = PROVINCE_DISPLAY[province]
            writer.predicate(row.id, "geo", geo)   # scope the shared province-level facts to this geo
            for dtype, series in block["data"].items():
                _persist_series(writer, source_id, geo_type, geo, row, dtype, series, substance_map)
            _persist_additional(writer, block, source_id, geo_type, geo, row)

        elif shape == "geo_series":
            for dtype, geo_dict in block["data"].items():
                for geo, series in geo_dict.items():
                    _persist_series(writer, source_id, geo_type, geo, row, dtype, series, substance_map)

        elif shape == "pie_nested":
            tot = additional_metric("Total Samples")
            writer.predicate(row.id, "additional_metric", tot)
            for geo, year_dict in block["data"]["counts"].items():
                for year, drug_dict in year_dict.items():
                    for drug, value in drug_dict.items():
                        writer.point(source_id, geo_type, geo, year, row.metric, "counts",
                                     None, None, row.dimension2_type, drug, value)
            # Total Samples (table-only) per health authority / year
            for geo, tabular in block.get("tabular_data", {}).items():
                totals = tabular.get("Total Samples", [])
                years = list(block["data"]["counts"].get(geo, {}).keys())
                for i, year in enumerate(years):
                    if i < len(totals):
                        writer.point(source_id, geo_type, geo, year, tot, "additional_rows",
                                     ADDITIONAL_DIM_TYPE, "Total Samples", None, None, totals[i])

        elif shape == "regional":
            for geo, year_dict in block["data"].get("counts", {}).items():
                for year, drug_dict in year_dict.items():
                    for drug, result_dict in drug_dict.items():
                        for result_key, count_list in result_dict.items():
                            result = result_key[:-2] if result_key.endswith("_y") else result_key
                            writer.point(source_id, geo_type, geo, year, row.metric, "counts",
                                         row.dimension_type, drug,
                                         row.dimension2_type, result, count_list[0])

        elif shape == "category_treemap":
            # Month-grain facts; geo is an ordered "||"-joined level composite.
            for geo, month_dict in block["data"].get("counts", {}).items():
                for month, cat_dict in month_dict.items():
                    for category, drug_dict in cat_dict.items():
                        for drug, value in drug_dict.items():
                            writer.point(source_id, geo_type, geo, month, row.metric, "counts",
                                         row.dimension_type, category,
                                         row.dimension2_type, drug, value, time_frame_type="month")


def _persist_series(writer, source_id, geo_type, geo, visual, dtype, series, substance_map):
    """Persist one (data_type, geo) series block (shared by flat_series and geo_series): every non-x
    key is a legacy series key decoded into (substance, disaggregator) dims, one fact per (year, value)."""
    x = series.get("x", [])
    for series_key, values in series.items():
        if series_key == "x":
            continue
        dim_val, dim2_val = encode_series_key(visual, series_key, substance_map)
        dim_type = "substance" if dim_val is not None else None
        for i, year in enumerate(x):
            if i < len(values):
                writer.point(source_id, geo_type, geo, year, visual.metric, dtype,
                             dim_type, dim_val, visual.dimension2_type, dim2_val, values[i])


def _persist_additional(writer, block, source_id, geo_type, geo, visual):
    """Persist additional (table-only) rows for a flat visual + record their metrics as predicates."""
    x = _primary_x(block)
    for label, values in block.get("additional_rows", {}).items():
        metric = additional_metric(label)
        writer.predicate(visual.id, "additional_metric", metric)
        for i, year in enumerate(x):
            if i < len(values):
                writer.point(source_id, geo_type, geo, year, metric, "additional_rows",
                             ADDITIONAL_DIM_TYPE, label, None, None, values[i])


# Test code below
if __name__ == '__main__':
    # The cleaners write straight to the DB now and need an app context + a FactWriter, so run a
    # regeneration via the CLI instead, e.g.:  flask gen-visuals --only canada
    pass