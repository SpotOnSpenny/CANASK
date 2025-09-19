# Python Standard Library Dependencies
import os
import json
import datetime
import re

# External Dependency Imports
import pandas
import plotly
import plotly.figure_factory

# Internal Dependency Imports


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
def export_nat_drug_toxicity_deaths():
    # Find what national data exists and clean it up to get what we need (toxicity deaths by province by year)
    national_raw = pull_data(["nationalHealthInfobase"])
    provincial_dfs = {}
    raw_df = national_raw["nationalHealthInfobase"]["dataframe"]
    provinces = ["British Columbia", "Alberta", "Saskatchewan", "Manitoba", "Ontario", "Quebec", "New Brunswick", "Nova Scotia", "Prince Edward Island", "Newfoundland and Labrador"]
    deaths_filter = raw_df["Source"] == "Deaths"
    stat_filter = raw_df["Type_Event"] == "Total apparent opioid toxicity deaths"
    period_filter = raw_df["Time_Period"] == "By year"
    unit_filter = raw_df["Unit"] == "Number"
    for province in provinces:
        province_filter = raw_df["Region"] == province
        provincial_dfs[province] = {}
        provincial_dfs[province]["sources"] = [{
            "name": "Opioid- and Stimulant-related Harms in Canada",
            "last_updated": national_raw["nationalHealthInfobase"]["date_updated"],
            "url": "https://health-infobase.canada.ca/substance-related-harms/opioids-stimulants/"
        }]
        provincial_dfs[province]["data"] = raw_df[deaths_filter & stat_filter & period_filter & unit_filter & province_filter]
        # Limit the column to deaths and year
        provincial_dfs[province]["data"] = provincial_dfs[province]["data"][["Year_Quarter", "Value"]]
        provincial_dfs[province]["data"].rename(columns={"Year_Quarter": "Year"}, inplace=True)

    # Replace national data with what provincial data we have and make note of the replaced data points for an about this data section
    sask_raw = pull_data(["skPubCentre"])
    sask_filtered = filter_data(sask_raw, ["ConfirmedDrugToxicityDeathsbyMannerofDeath"])
    sask_total_deaths = sask_filtered[0]["dataframe"].loc[sask_filtered[0]["dataframe"]["Year"] == "Total"]
    provincial_dfs["Saskatchewan"]["sources"] = [{
        "name": "Saskatchewan Coroners Service",
        "last_updated": sask_filtered[0]["date_updated"],
        "url": "https://publications.saskatchewan.ca/#/products/90505"
    }]
    for column_name, column_data in sask_total_deaths.iloc[:, 1:].items():
        column_value = column_data.to_list()[0]
        mask = provincial_dfs["Saskatchewan"]["data"]["Year"].str.contains(re.escape(column_name), case=False, na=False)
        provincial_dfs["Saskatchewan"]["data"].loc[mask, "Value"] = column_value
        provincial_dfs["Saskatchewan"]["data"].loc[mask, "Year"] = column_name

    bc_raw = pull_data(["bcCoronersReport"])
    bc_filtered = filter_data(bc_raw, ["Unregulated Drug Deaths by Month"])
    bc_total_deaths = bc_filtered[0]["dataframe"].iloc[-1]
    provincial_dfs["British Columbia"]["sources"] = [{
        "name": "BC Coroners Service",
        "last_updated": bc_filtered[0]["date_updated"],
        "url": "https://app.powerbi.com/view?r=eyJrIjoiM2Y5YzRjNzQtMzAyNS00NWFiLWI3MDktMzI5NWQ3YmVhNmZjIiwidCI6IjZmZGI1MjAwLTNkMGQtNGE4YS1iMDM2LWQzNjg1ZTM1OWFkYyJ9"
    }]
    provincial_dfs["British Columbia"]["data"]["Year"] = provincial_dfs["British Columbia"]["data"]["Year"].str.strip()
    for column_name, column_data in bc_total_deaths.iloc[1:].items():
        mask = provincial_dfs["British Columbia"]["data"]["Year"].str.contains(re.escape(column_name.strip()), case=False, na=False)
        if mask.any():
            provincial_dfs["British Columbia"]["data"].loc[mask, "Value"] = column_data
            provincial_dfs["British Columbia"]["data"].loc[mask, "Year"] = column_name
    
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
                    source["Province"] = "all other provincial"
                else:
                    source["Province"] = province_data
                sources.append(source)
        province_abbreviation = province_keys[province_data]
        total_tox_deaths_data["y_axes"][f"{province_abbreviation}_line_y"] = provincial_dfs[province_data]["data"]["Value"].to_list()
        if len(provincial_dfs[province_data]["data"]["Year"].to_list()) > len(longest_year_line):
            longest_year_line = provincial_dfs[province_data]["data"]["Year"].to_list()
    total_tox_deaths_data["x_axes"]["can_line_x"] = [year.replace(u"\xa0", "") for year in longest_year_line]
    total_tox_deaths_data["sources"] = sources
    total_tox_deaths_data["about_these_data"] = """These data are collected from provincial authorities when available, and supplemented with national reports to fill in unavailable provincial data. If you are aware of, or have access to, a provincial data source that can be used to supplement or replace national data, please contact us at email@email.com. Please also be aware that the most recent data points may be incomplete as a result of some provincial data being unpublished at this time. Kindly refer to the "last updated" date for information on when the data for each province was last published.
    The data sources used in this visualization are as follows:"""

    # Create the statistics for the /100,000 population
    # Get the population data
    population_data = pull_data(["nationalPopulationData"])
    population_data = filter_data(population_data, ["nationalPopulationData"])
    population_df = population_data[0]["dataframe"]
    
    # diivide each popululation by 100,000
    for province in provinces:
        for index, year in enumerate(total_tox_deaths_data["x_axes"]["can_line_x"]):
            population = population_df.loc[population_df["GEO"] == province].loc[population_df["REF_DATE"] == int(year)]["VALUE"].values[0]
            hundred_k = population / 100000
            total_tox_deaths_data["y_axes_per_100k"][f"{province_keys[province]}_line_y_per_100k"] = [round((float(value) / hundred_k), 2) for value in total_tox_deaths_data["y_axes"][f"{province_keys[province]}_line_y"]]
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

def sask_visual_data():
    data = pull_data(["skPubCentre"])
    sk_data = filter_data(data, ["ConfirmedDrugToxicityDeathsbyMannerofDeath,2016-2024", "Breakdown of Opioid Drugs Identified in Confirmed Drug Toxicity Deaths by Manner of Death, 2016 - 2024", "Breakdown of Benzodiazepine Drugs Identified in Confirmed Drug Toxicity Deaths by Manner of Death, 2024"])
    death_df = sk_data[0]
    drug_df = sk_data[1]
    # Clean the data to get the aggregate values we need
    drug_deaths = {}
    drug_percents = {}
    sk_raw_data = {}
    years = [year for year in sk_data[0].columns if year != "Year"]
    total_deaths = [deaths for deaths in death_df.loc[death_df["Year"]=="Total"].values[0] if deaths != "Total"]
    drugs = ["Codeine", "Fentanyl", "Heroin", "Hydrocodone", "Hydromorphone", "Methadone", "Morphine", "Oxycodone", "Buprenorphine"]
    drug_dict = {}
    for index, row in drug_df.iterrows():
        if row["Year"] not in drug_dict.keys():
            drug_dict[row["Year"]] = {}
        for drug in drugs:
            if drug not in drug_dict[row["Year"]].keys():
                drug_dict[row["Year"]][drug] = 0
            if row[drug].isnumeric():
                drug_dict[row["Year"]][drug] += int(row[drug])
    for drug in drugs:
        drug_deaths[drug] = [drug_dict[year][drug] for year in years]
        sk_raw_data[f"Deaths Resulting From {drug}"] = [drug_dict[year][drug] for year in years]
        sk_raw_data[f"Percent of Deaths Resulting From {drug}"] = [round(((int(drug_dict[year][drug]) / int(total_deaths[index])) * 100), 2) for index, year in enumerate(years)]
    graph_data = {
        "years": years,
        "total_deaths": total_deaths,
        "drug_deaths": drug_deaths,
        "raw_data": sk_raw_data
    }

    with open("static/js/sask_vis.json", "w") as file:
        json.dump(graph_data, file)

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
            population = population_data[int(year)]
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
                population = population_data[int(year)]
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
            population = population_data[int(year)]
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

# Export all data to a json file, using URL friendly province names as keys
# These keys will be passed as parameters to javascript so that we can pull the right data for each page
def export_data_json():
    data = {
        "british-columbia": v1_BC_export_clean(),
        "alberta": v1_AB_export_clean(),
        "saskatchewan": v1_SK_export_clean(),
        "manitoba": v1_MB_export_clean(),
        "new-brunswick": v1_NB_export_clean()
    }
    # Write the data to a json file
    with open(os.path.join(os.path.abspath(os.path.dirname(__file__)), "static/js/visual_data.json"), "w") as file:
        json.dump(data, file, indent=4)

# Test code below
if __name__ == '__main__':
    #data = v1_SK_export_clean()
    # data = v1_clean_national_data("New Brunswick")
    # print(data)
    export_data_json()