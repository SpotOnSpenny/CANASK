"""The drug-checking found-substance crosswalk (app_config/drugcheck_found_groups.json) and the
per-sample extractor behind the "What's in the Sample?" visual. Raw values below are real spellings
from the harmonized CCSA workbook -- a renamed group or a dropped token must fail here, not silently
move a line on the chart."""
import pytest

from data_viz import drugcheck_substances as ds


def _sample(expected="Ketamine", **overrides):
    """One checked-sample row (internal column names): every strip Not done, no FTIR identifications."""
    row = {"Expected Drug (1)": expected}
    for col in ds.STRIP_COLS:
        row[col] = "Not done"
    for i in range(1, 6):
        row[f"FTIR ({i})"] = None
        row[f"FTIR ({i}) Drug Category"] = None
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def _clean_unmapped():
    ds.reset_unmapped()
    yield
    ds.reset_unmapped()


class TestCrosswalkStructure:
    def test_groups_keep_file_order_with_fentanyl_first(self):
        keys = [g["key"] for g in ds.group_order()]
        assert keys[0] == "fentanyl_analogues"
        assert "cutting_agents" in keys and "medetomidine" in keys
        assert len(keys) == len(set(keys))

    def test_every_group_has_a_label_and_a_substance(self):
        for key, group in ds.load_found_groups()["groups"].items():
            assert group["label"], key
            assert group["substances"], key

    def test_no_token_belongs_to_two_groups(self):
        seen = {}
        for key, group in ds.load_found_groups()["groups"].items():
            for label, tokens in group["substances"].items():
                for token in tokens:
                    assert token not in seen, f"{token!r} in both {seen.get(token)} and {key}"
                    seen[token] = key
                    assert token == ds.norm_substance(token), f"{token!r} is not a normalized token"

    def test_strips_and_fallbacks_point_at_known_groups(self):
        data = ds.load_found_groups()
        groups = data["groups"]
        for col, (group, label) in data["strips"].items():
            assert col in ds.STRIP_COLS, col
            assert group in groups, col
            assert label in groups[group]["substances"], (col, label)
        for token, group in data["category_fallback"].items():
            assert group in groups, token
            assert token == ds.norm_substance(token)

    def test_ignore_tokens_are_not_also_mapped(self):
        data = ds.load_found_groups()
        mapped = {t for g in data["groups"].values() for ts in g["substances"].values() for t in ts}
        assert not (set(data["ignore"]) & mapped)


class TestResolveFtir:
    @pytest.mark.parametrize("raw, category, expected", [
        # fentanyl family: six observed spellings of para-fluorofentanyl, plus the strip-free FTIR hit
        ("Parafluoro fentanyl", "opioïdes", ("fentanyl_analogues", "Para-fluorofentanyl")),
        ("Para-fluorofentanyl", "Opioids", ("fentanyl_analogues", "Para-fluorofentanyl")),
        ("Paraflurofentanyl", "Opioids", ("fentanyl_analogues", "Para-fluorofentanyl")),
        ("Ortho-flurofentanyl", "Opioids", ("fentanyl_analogues", "Ortho-fluorofentanyl")),
        ("Carfentanil HCL", "Opioids", ("fentanyl_analogues", "Carfentanil")),
        ("FentanylAnalogue", "Opioids", ("fentanyl_analogues", "Fentanyl")),
        ("Etonitazepine", "Opioids", ("nitazenes", "Etonitazepyne")),
        ("Ethylene Nitazene HCL", "Stimulants", ("nitazenes", "Ethylene nitazene")),   # source mis-categorised
        ("héroïne", "opioïdes", ("other_opioids", "Heroin")),
        ("Désalkylgidazépam", "sédatifs", ("benzodiazepines", "Desalkylgidazepam")),
        ("Aplrazolam", "Sedatives/Hypnotics", ("benzodiazepines", "Alprazolam")),
        ("Medetomedine", "Other drug", ("medetomidine", "Medetomidine")),
        ("Cocaïne HCl", "Stimulants", ("cocaine", "Cocaine")),
        ("Cocaine freebase", "Stimulants", ("cocaine", "Crack cocaine")),
        ("Methamphetemine HCL", "Stimulants", ("methamphetamine", "Methamphetamine")),
        ("Méthamphtétamine", "Stimulants", ("methamphetamine", "Methamphetamine")),
        ("dextroamphétamine", "Stimulants", ("methamphetamine", "Amphetamine")),
        ("MDMA - (3,4-Methylenedioxymethamphetamine) HCL", "Stimulants", ("mdma", "MDMA")),
        ("MDA (3,4-Methylenedioxyamphetamine)", "Stimulants", ("mdma", "MDA")),
        ("4-MMC", "Stimulants", ("other_stimulants", "4-MMC")),
        ("Kétamine hcl", "dissociatif", ("dissociatives", "Ketamine")),
        ("DMXE HClL", "Stimulants", ("dissociatives", "DMXE")),   # a dissociative the source files as a stimulant
        ("2C-B HBr", "Hallucinogens", ("hallucinogens", "2C-B")),
        ("1.4 Butanediol", "sédatifs/hypnotiques", ("other_sedatives", "1,4-Butanediol")),
        ("THC", "cannabinoïdes", ("cannabinoids", "THC")),
        ("Caféine", "Stimulants", ("cutting_agents", "Caffeine")),   # caffeine is a cut, whatever the source says
        ("Meso-Erythitol", "Other drug", ("cutting_agents", "Erythritol")),
        ("Microcristaline cellusloe", "Non-psychoactive substance", ("cutting_agents", "Microcrystalline cellulose")),
        ("Phénacétine", "autre médicament", ("cutting_agents", "Phenacetin")),
        ("Dimelthyl sulfone", "Other drug", ("cutting_agents", "Dimethyl sulfone")),
        ("Eau", "substance non psychoactive", ("cutting_agents", "Water")),
        ("Huile indéterminé", "substance non psychoactive", ("cutting_agents", "Unidentified oil")),
        ("Lidocaine", None, ("cutting_agents", "Lidocaine")),
        ("Quetiapine", "Other drug", ("other_pharma", "Quetiapine")),
        # spellings that only surface after the Cycle-1 column-shift repair moves them into FTIR columns
        ("GHB liquide", "Sedatives", ("other_sedatives", "GHB")),
        ("Microcrisatline cellulose", "substance non psychoactive", ("cutting_agents", "Microcrystalline cellulose")),
        ("Sodium d-pantothenate", None, ("cutting_agents", "Sodium pantothenate")),
        # the 2026-11 Cycle 1-6 export (data to 2026-09-30)
        ("Cocaine HCI", "Stimulants", ("cocaine", "Cocaine")),   # capital I for l
        ("Acetylfentanyl base", "Opioids", ("fentanyl_analogues", "Acetylfentanyl")),
        ("Heroine HCL", "Opioids", ("other_opioids", "Heroin")),
        ("Ethylbromazolam", "Sedatives/Hypnotics", ("benzodiazepines", "Ethylbromazolam")),
        ("Desalkygidazepam", "Sedatives/Hypnotics/Hypnotics/Hypnotics", ("benzodiazepines", "Desalkylgidazepam")),
        ("Metamphetamine HCl", "Stimulants", ("methamphetamine", "Methamphetamine")),
        ("Eutylune HCL", "Other drug", ("other_stimulants", "Eutylone")),
        ("O-PCPr HCI", "Dissociatives", ("dissociatives", "O-PCPr")),
        ("5-meO-DMT", "Hallucinogens", ("hallucinogens", "5-MeO-DMT")),
        ("Levamisole base suspecté", None, ("cutting_agents", "Levamisole")),
        ("Plâtre", "Non-psychoactive substance", ("cutting_agents", "Plaster")),
        ("Pregabalin", "Sedatives/Hypnotics/Hypnotics", ("other_pharma", "Pregabalin")),
    ])
    def test_real_spellings_resolve(self, raw, category, expected):
        assert ds.resolve_ftir(raw, category) == expected

    @pytest.mark.parametrize("raw", ["Inconclusive", "Non concluant", "Uncertain Match", "Match incertain",
                                     "OTHER", "élément incertain", "Hemitartrate incertain", "N/A", "", None,
                                     "Aucun match concluant", "Autre substance non-identifiée suspectée",
                                     "Bandelettes fentanyl, benzodiazepine et médétomidine positives"])
    def test_identifies_nothing(self, raw):
        assert ds.resolve_ftir(raw, "Stimulants") is None
        assert ds.unmapped_tokens() == {}

    def test_unmapped_token_falls_back_on_the_source_category_and_is_recorded(self):
        assert ds.resolve_ftir("Brand New Stimulant", "Stimulants") == ("other_stimulants", "Brand New Stimulant")
        assert ds.resolve_ftir("Nouveau sucre", "substance non psychoactive") == ("cutting_agents", "Nouveau sucre")
        assert ds.unmapped_tokens() == {
            "brandnewstimulant": {"raw": "Brand New Stimulant", "category": "Stimulants", "group": "other_stimulants"},
            "nouveausucre": {"raw": "Nouveau sucre", "category": "substance non psychoactive", "group": "cutting_agents"},
        }

    def test_cascaded_sedative_category_still_falls_back(self):
        # The 2026-11 export multiplied the "/Hypnotics" suffix by a cascaded find-and-replace.
        assert ds.resolve_ftir("Brand New Sedative", "Sedatives/Hypnotics/Hypnotics/Hypnotics") == (
            "other_sedatives", "Brand New Sedative")
        assert ds.resolve_ftir("Mystery Cut", "Other") == ("cutting_agents", "Mystery Cut")

    def test_unmapped_token_without_a_usable_category_is_dropped_but_recorded(self):
        assert ds.resolve_ftir("Mystery Compound", "N/A") is None
        assert ds.resolve_ftir("Mystery Compound", None) is None
        assert ds.unmapped_tokens()["mysterycompound"]["group"] is None


class TestFoundSubstances:
    def test_ftir_hits_across_columns(self):
        row = _sample("Cocaine", **{"FTIR (1)": "Cocaine HCL", "FTIR (1) Drug Category": "Stimulants",
                                    "FTIR (3)": "Caffeine", "FTIR (3) Drug Category": "Other drug"})
        assert ds.found_substances(row) == {("cocaine", "Cocaine"), ("cutting_agents", "Caffeine")}

    def test_positive_strips_count_and_other_readings_do_not(self):
        row = _sample("Fentanyl", **{"Medetomidine test strip": "Positif", "Benzodiazepine test strip": "Positive",
                                     "Nitazene test strip": "Negative", "Xylazine test strip": "Not available"})
        assert ds.found_substances(row) == {("medetomidine", "Medetomidine"),
                                            ("benzodiazepines", "Benzodiazepine (test strip)")}

    def test_fentanyl_strip_and_ftir_fentanyl_merge_into_one_substance(self):
        row = _sample("Fentanyl", **{"Fentanyl test strip": "Positive", "FTIR (1)": "Fentanyl",
                                     "FTIR (1) Drug Category": "Opioids"})
        assert ds.found_substances(row) == {("fentanyl_analogues", "Fentanyl")}

    def test_by_method_keeps_ftir_and_strip_apart(self):
        row = {"FTIR (1)": "Fentanyl", "FTIR (2)": "Caffeine", "Fentanyl test strip": "Positif",
               "Benzodiazepine test strip": "Positive"}
        assert ds.found_substances_by_method(row) == {
            ("fentanyl_analogues", "Fentanyl", "ftir"), ("fentanyl_analogues", "Fentanyl", "strip"),
            ("cutting_agents", "Caffeine", "ftir"), ("benzodiazepines", "Benzodiazepine (test strip)", "strip"),
        }
        assert ds.found_substances(row) == {("fentanyl_analogues", "Fentanyl"), ("cutting_agents", "Caffeine"),
                                            ("benzodiazepines", "Benzodiazepine (test strip)")}

    def test_two_spellings_of_one_substance_count_once(self):
        row = _sample("Fentanyl", **{"FTIR (1)": "Erythritol", "FTIR (2)": "Meso-Erythitol"})
        assert ds.found_substances(row) == {("cutting_agents", "Erythritol")}

    def test_inconclusive_and_blank_cells_ignored(self):
        row = _sample("Ketamine", **{"FTIR (1)": "Uncertain Match", "FTIR (2)": "N/A"})
        assert ds.found_substances(row) == set()

    def test_shifted_junk_in_a_strip_column_is_not_a_positive(self):
        # The Medetomidine column carries FTIR category text in a few un-repaired rows.
        row = _sample("MDMA", **{"Medetomidine test strip": "Not in DAS classification system"})
        assert ds.found_substances(row) == set()
