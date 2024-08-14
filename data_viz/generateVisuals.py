# Python Standard Library Dependencies
import os
import json
import datetime

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
            match file.split(".")[1]:
                case "csv":
                    sheets[source] = pandas.read_csv(os.path.join(output_dir, file))
                case "xlsx":
                    dataframes = pandas.read_excel(os.path.join(output_dir, file), sheet_name=None).values()
                    for dataframe in dataframes:
                        name = list(filter(lambda value: True if "Unnamed" not in value and value != "NaN" else False, dataframe.columns))[0]
                        dataframe.set_flags(allows_duplicate_labels=False)
                        dataframe.columns = dataframe.iloc[0]
                        dataframe.dropna(axis=0, inplace=True)
                        dataframe = dataframe.drop(dataframe.columns[[0]], axis=1).reset_index(drop=True)
                        sheets[name] = dataframe
        else:
            raise FileNotFoundError(f"Data source {source} not found in the output directory!")
    return sheets

# Helper function to pull the data from the provided source into a dataframe
def filter_data(data: dict, find_these: list):
    dataframes = []
    for key in data.keys():
        if any(find_this.split(",")[0].lower().replace(" ", "") == key.split(",")[0].lower().replace(" ", "") for find_this in find_these):
            dataframes.append(data[key])
    return dataframes

# Function to generate the graph for drugs involved in toxicity deaths
def drug_type_visual(data: dict):
    # Clean the data to get the aggregate values we need (SK Publication Centre Specific)
    sask_raw = filter_data(data, ["ConfirmedDrugToxicityDeathsbyMannerofDeath,2016-2024", "Breakdown of Opioid Drug s Identified in Confirmed Drug Toxicity  Deaths  by Manner of Death,  2016 - 2024", "BreakdownofBenzodiazepineDrugsIdentifiedinConfirmedDrugToxicityDeathsbyMannerofDeath,2024"])
    sask_clean = {}
    for frame in sask_raw:
        for index, row in frame.iterrows():
            if row["Year"] not in sask_clean.keys():
                year = row["Year"]
                sask_clean[year] = {}
            else:
                year = row["Year"]
            for key in row.keys():
                if key != "Year" and key != "MannerOfDeath" and key not in sask_clean[year].keys() and row[key].isnumeric():
                    sask_clean[year][key] = int(row[key])
                elif key != "Year" and key != "MannerOfDeath" and key in sask_clean[year].keys() and row[key].isnumeric():
                    sask_clean[year][key] += int(row[key])
    # Condense the SK data into similar categories to BC

        # Benzodiazipines
        # Cocaine
        # Fentanyl
        # Meth/Amphetamines
        # Other Opioids
        # Other Stimulants
        # Fentanyl Analogues

    # Clean the data to get the aggregate values we need (BC Coroners Report Specific)
    bc_raw = filter_data(data, ["Unregulated Drug Deaths by Month, 2014-2024", "Unregulated Drug Deaths by Drug Types Relevant to Death"])
    bc_totals = {year.replace(u"\xa0", ""): int(bc_raw[0].at[12, year]) for year in bc_raw[0].columns[1:]}
    bc_clean = {}
    for name, item in bc_raw[1].items():
        if name == bc_raw[1].columns[0]:
            drugs = item
        else:
            name = name.replace(u"\xa0", "")
            bc_clean[name] = {}
            for index, row in enumerate(item):
                bc_clean[name][drugs[index]] = (int(bc_raw[0].at[12, u"{0}\xa0".format(name)]) * (float(row.replace("%", "")) / 100)).__floor__()

    # Create traces for BC
    bc_line_x = [key for key in bc_totals.keys() if bc_clean.get(key, False)]
    bc_line_y = [bc_totals[year] for year in bc_line_x]
    bc_drugs = [item for sublist in [list(key.keys()) for key in list(bc_clean.values())] for item in sublist]
    bc_axies = {
        "years": [year for year in bc_clean.keys()],
        "drugs": sorted(list(set(bc_drugs)))
    }
    bc_drug_traces = {}
    for drug in bc_axies["drugs"]:
        bc_drug_traces[drug] = plotly.graph_objects.Bar(
            x=bc_axies["years"],
            y=[bc_clean[year][drug] for year in bc_axies["years"]],
            name=drug if drug != "Meth/amph" else "Meth and Amphetamines",
        )
    bc_drug_traces["BC Totals"] = plotly.graph_objects.Scatter(
        x=bc_line_x,
        y=bc_line_y,
        name="BC Total Deaths",
        marker={"color": "gray"}
    )
    #fig = plotly.graph_objects.Figure(data=list(bc_drug_traces.values())) # Uncomment this line to see the BC data in testing

    # Create traces for SK
    sask_line_x = [key for key in sask_clean["Total"].keys()]
    sask_line_y = [sask_clean["Total"][year] for year in sask_line_x]
    sask_drugs = [drug for year in sask_clean if str(year).isnumeric() for drug in sask_clean[year]]
    sask_axies = {
        "years": [year for year in sask_clean.keys() if str(year).isnumeric()],
        "drugs": sorted(list(set(sask_drugs)))
    }
    sask_drug_traces = {}
    for drug in sask_axies["drugs"]:
        sask_drug_traces[drug] = plotly.graph_objects.Bar(
            x=sask_axies["years"],
            y=[sask_clean[year].get(drug, None) for year in sask_axies["years"]],
            name=drug
        )
    sask_drug_traces["SK Totals"] = plotly.graph_objects.Scatter(
        x=sask_line_x,
        y=sask_line_y,
        name="SK Total Deaths",
        marker={"color": "gray"}
    )
    # fig = plotly.graph_objects.Figure(data=list(sask_drug_traces.values())) # Uncomment this line to see the BC data in testin
    # Create traces for combined Canada Wide Data
    graph_data = {"total_deaths": {}}
    graph_data["total_deaths"]["can_line_x"] = [year for year in bc_line_x if year in sask_clean["Total"].keys()]
    graph_data["total_deaths"]["bc_line_y"] = [bc_totals[year] for year in graph_data["total_deaths"]["can_line_x"]]
    graph_data["total_deaths"]["sk_line_y"] = [sask_clean["Total"][year] for year in graph_data["total_deaths"]["can_line_x"]]
    with open("static/js/visualization_data.json", "w") as file:
        json.dump(graph_data, file)

def bc_visual_data():
    data = pull_data(["bcCoronersReport", "bcDrugSense"])
    graph_data = {}
    bc_drug_sense = filter_data(data, ["bcDrugSense"])[0]

    # Seperate data into years
    data_by_year = {}
    starting_year = 2018
    current_year = datetime.datetime.now().year
    range_years = range(starting_year, current_year + 1)
    for year in range_years:
        data_by_year[str(year)] = bc_drug_sense.loc[bc_drug_sense["Visit Date"].str.contains(str(year))]

    # Pull data related to the number of drugs that contained fentanyl from the BC Drug Sense data
    fent_benz_raw = {
        "years": [],
        "Total Samples": [],
        "Fentanyl Pos. Samples": [],
        "Percent Fentanyl Pos.": [],
        "Benzodiazepine Pos. Samples": [],
        "Percent Benzodiazepine Pos.": []
    }
    percent_fentanyl_data = {
        "x": [],
        "y": [],
    }
    for year, data in data_by_year.items():
        fentanyl_pos = data.loc[data["Fentanyl Strip"] == "Pos"]
        percent_fentanyl = round(((len(fentanyl_pos) / len(data)) * 100), 2)
        percent_fentanyl_data["x"].append(year)
        percent_fentanyl_data["y"].append(percent_fentanyl)
        fent_benz_raw["years"].append(year)
        fent_benz_raw["Total Samples"].append(len(data))
        fent_benz_raw["Fentanyl Pos. Samples"].append(len(fentanyl_pos))
        fent_benz_raw["Percent Fentanyl Pos."].append(percent_fentanyl)
    graph_data["bc_percent_fentanyl"] = percent_fentanyl_data

    # Pull the data related to the number of drugs that contained benzos from the BC Drug Sense data
    percent_benzo_data = {
        "x": [],
        "y": []
    }
    for year, data in data_by_year.items():
        benzo_pos = data.loc[data["Benzo Strip"] == "Pos"]
        percent_benzo = round(((len(benzo_pos) / len(data)) * 100), 2)
        percent_benzo_data["x"].append(year)
        percent_benzo_data["y"].append(percent_benzo)
        fent_benz_raw["Benzodiazepine Pos. Samples"].append(len(benzo_pos))
        fent_benz_raw["Percent Benzodiazepine Pos."].append(percent_benzo)
    graph_data["bc_raw_fent_benz"] = fent_benz_raw
    graph_data["bc_percent_benzo"] = percent_benzo_data

    # Pull the data related to category of drugs from the BC Drug Sense data as percentage of visits
    drug_catagory_raw = {
        "years": list(data_by_year.keys()),
        "Total Samples": [len(data) for data in data_by_year.values()],
    }
    drugs_by_category = {}
    drug_categories = bc_drug_sense["Category"].unique()
    for category in drug_categories:
        drugs_by_category[category] = {
                "x": [],
                "y": []
            }
        for year, data in data_by_year.items():
            category_data = data.loc[data["Category"] == category]
            drugs_by_category[category]["x"].append(year)
            drugs_by_category[category]["y"].append(round((len(category_data)/len(data) * 100), 2))
            if category not in drug_catagory_raw.keys():
                drug_catagory_raw[category] = []
            drug_catagory_raw[category].append(len(category_data))
            if f"Percent of Samples {category}" not in drug_catagory_raw.keys():
                drug_catagory_raw[f"Percent of Samples {category}"] = []
            drug_catagory_raw[f"Percent of Samples {category}"].append(round((len(category_data)/len(data) * 100), 2))
    graph_data["bc_drugs_by_category"] = drugs_by_category
    graph_data["bc_raw_drug_category"] = drug_catagory_raw


    # Pull data related to Spectrometer results to stratify drugs by type
    opioids_by_type = {}
    opioid_type_raw = {
        "years": list(data_by_year.keys()),
        "Total Opioid Samples": [len(data.loc[data["Category"] == "Opioid"]) for data in data_by_year.values()],
    }
    categories = ["Codeine", "Fentanyl", "Heroin", "Hydrocodone", "Hydromorphone", "Methadone", "Morphine", "Oxycodone", "Buprenorphine"]
    for opioid in categories:
        opioids_by_type[opioid] = {
            "x": [],
            "y": []
        }
        opioid_type_raw[opioid] = []
        opioid_type_raw[f"Percent of Samples {opioid}"] = []
        for year, data in data_by_year.items():
            opioid_data = data.loc[data["Category"] == "Opioid"].fillna("No Data")
            type_data = opioid_data.loc[opioid_data["Spectrometer"].str.contains(opioid, case=False)]
            opioids_by_type[opioid]["x"].append(year)
            opioids_by_type[opioid]["y"].append(round((len(type_data)/len(opioid_data) * 100), 2))
            opioid_type_raw[opioid].append(len(type_data))
            opioid_type_raw[f"Percent of Samples {opioid}"].append(round((len(type_data)/len(opioid_data) * 100), 2))
    graph_data["bc_opioids_by_type"] = opioids_by_type
    graph_data["bc_raw_opioid_type"] = opioid_type_raw
    
    with open("static/js/bc_vis.json", "w") as file:
        json.dump(graph_data, file)


# Test code below
if __name__ == '__main__':
    bc_visual_data()