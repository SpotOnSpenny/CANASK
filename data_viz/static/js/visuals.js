// Dictionary of visuals that each province has
const visuals = {
    "default-visuals": {
        "british-columbia": "drug_death_heatmap",
        "alberta": "opioid_deaths_by_age",
        "manitoba": "deaths_by_age"
        },
    "british-columbia": {
        "drug_death_heatmap": {
            "type": "heatmap",
            "data-types": ["counts"],
            "menu-parent": "Deaths and Demographics",
            "menu-name": "Drug Toxicity Deaths by Health Authority",
            "level": 1,
            "vis-parent": null,
            "next-vis": "deaths_by_sex_line"
        },
        "deaths_by_sex_line": {
            "type": "line",
            "data-types": ["counts", "rates"],
            "menu-parent": null,
            "menu-name": null,
            "level": 2,
            "vis-parent": "drug_death_heatmap",
            "next-vis": null
        },
        "drug_supply_by_year": {
            "type": "line",
            "data-types": ["counts", "rates"],
            "menu-parent": "Drug Supply",
            "menu-name":"Drugs by Category",
            "level": 1,
            "vis-parent": null,
            "next-vis": null
        },
        "fent_benz_by_year": {
            "type": "line",
            "data-types": ["counts", "rates"],
            "menu-parent": "Drug Supply",
            "menu-name": "Presence of Fentanly and Benzodiazepines",
            "level": 1,
            "vis-parent": null,
            "next-vis": null
        },
        "opioid_types_by_year": {
            "type": "line",
            "data-types": ["counts", "rates"],
            "menu-parent": "Drug Supply",
            "menu-name": "Presence of Opioid Types",
            "level": 1,
            "vis-parent": null,
            "next-vis": null
        },
        "toxicity_deaths_per_drug_by_year": {
            "type": "bar",
            "data-types": ["counts", "rates", "percentages"],
            "menu-parent": "Deaths and Demographics",
            "menu-name": "Unregulated Drug Toxicity Deaths by Drug Type",
            "level": 1,
            "vis-parent": null,
            "next-vis": null
        },
        "drug_toxicity_deaths_by_age": {
            "type": "line",
            "data-types": ["counts", "rates"],
            "menu-parent": "Deaths and Demographics",
            "menu-name": "Unregulated Drug Toxicity Deaths by Age Group",
            "level": 1,
            "vis-parent": null,
            "next-vis": null
        },
        "drug_supply_geographically": {
            "type": "map",
            "data-types": null,
            "menu-parent": "Drug Supply",
            "menu-name": "Drug Supply by Health Authority",
            "level": 1,
            "vis-parent": null,
            "next-vis": "geographical_drug_supply_pie"
        },
        "geographical_drug_supply_pie": {
            "type": "pie", 
            "data-types": ["counts"],
            "menu-parent": null,
            "menu-name": null,
            "level": 2,
            "vis-parent": "drug_supply_geographically",
            "next-vis": "regional_drug_supply_breakdown"
        }, 
        "regional_drug_supply_breakdown":{
            "type": "bar",
            "data-types": ["counts"],
            "menu-parent": null,
            "menu-name": null,
            "level": 3,
            "vis-parent": "geographical_drug_supply_pie",
            "next-vis": null
        }
    },
    "alberta": {
        "opiod_deaths_by_age": {
            "type": "line",
            "data-types": ["counts", "percentages"],
            "menu-parent": "Deaths and Demographics",
            "menu-name": "Opioid Deaths by Age Group",
            "level": 1,
            "vis-parent": null,
            "next-vis": null
        },
        "deaths_by_drug_type": {
            "type": "bar",
            "data-types": ["counts", "rates", "percentages"],
            "menu-parent": "Deaths and Demographics",
            "menu-name": "Opioid Deaths by Drug Type",
            "level": 1,
            "vis-parent": null,
            "next-vis": null
        },
        "deaths_by_sex": {
            "type": "line",
            "data-types": ["counts", "rates", "percentages"],
            "menu-parent": "Deaths and Demographics",
            "menu-name": "Opioid Deaths by Sex",
            "level": 1,
            "vis-parent": null,
            "next-vis": null
        },
        "deaths_by_manner": {
            "type": "line",
            "data-types": ["counts", "rates", "percentages"],
            "menu-parent": "Deaths and Demographics",
            "menu-name": "Unregulated Drug Toxicity Deaths by Manner of Death",
            "level": 1,
            "vis-parent": null,
            "next-vis": null
        }
    },
    "manitoba": {
        "deaths_by_age": {
            "type": "line",
            "data-types": ["counts", "percentages"],
            "menu-parent": "Deaths and Demographics",
            "menu-name": "Opioid Deaths by Age Group",
            "level": 1,
            "vis-parent": null,
            "next-vis": null
        },
        "deaths_by_drug_type": {
            "type": "bar",
            "data-types": ["counts", "rates", "percentages"],
            "menu-parent": "Deaths and Demographics",
            "menu-name": "Opioid Deaths by Drug Type",
            "level": 1,
            "vis-parent": null,
            "next-vis": null
        },
        "deaths_by_sex": {
            "type": "line",
            "data-types": ["counts", "rates", "percentages"],
            "menu-parent": "Deaths and Demographics",
            "menu-name": "Opioid Deaths by Sex",
            "level": 1,
            "vis-parent": null,
            "next-vis": null
        },
        "deaths_by_manner": {
            "type": "line",
            "data-types": ["counts", "rates", "percentages"],
            "menu-parent": "Deaths and Demographics",
            "menu-name": "Unregulated Drug Toxicity Deaths by Manner of Death",
            "level": 1,
            "vis-parent": null,
            "next-vis": null
        }
    }
}
