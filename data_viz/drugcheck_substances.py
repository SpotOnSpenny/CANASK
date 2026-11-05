"""Found-substance extraction for the drug-checking "What's in the Sample?" visual.

Each checked sample carries up to five FTIR identifications (hand-entered, in English and French,
with salt forms and typos) and six test strips. This module turns one sample row into the set of
``(group_key, substance_label)`` pairs it contained, driven by the maintained crosswalk
``app_config/drugcheck_found_groups.json`` (see its ``_comment``). The cleaner in
``generate_visuals`` aggregates those pairs into facts; the client draws one line per label.

Pure functions, no DB. ``norm_substance`` / ``FTIR_COLS`` / ``STRIP_COLS`` live here (and are
re-exported by ``generate_visuals`` under their historical private names) so both modules share one
definition without a circular import."""
import json
import os
import re
import unicodedata
from functools import lru_cache

import pandas

CROSSWALK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_config", "drugcheck_found_groups.json")

FTIR_COLS = ["FTIR (1)", "FTIR (2)", "FTIR (3)", "FTIR (4)", "FTIR (5)"]
STRIP_COLS = [
    "Fentanyl test strip", "Benzodiazepine test strip", "Nitazene test strip",
    "Xylazine test strip", "MDMA Test strip", "Medetomidine test strip",
]
# A strip is credited only on an explicit positive reading (English or French); "Not done",
# "Not available", "Invalid" and the FTIR-category text that leaks into the Medetomidine column in a
# few un-repaired rows are all "no detection".
STRIP_POSITIVE = {"positive", "positif"}
# Sentinel dimension2 keys the cleaner emits beside the real "<group>|<label>" keys: the samples
# checked, and the samples containing ANY substance of a group. Labels must never collide with
# them or carry the "|" separator (load_found_groups enforces it; the client splits on "|").
TOTAL_KEY = "__total__"
ANY_LABEL = "__any__"


def norm_substance(value):
    """Canonical comparison token for a hand-entered substance name: accents decomposed and
    dropped, lowercased, every non-alphanumeric removed ("Cocaine HCl" / "Cocaïne" -> "cocainehcl"
    / "cocaine"). Returns "" for missing values."""
    if value is None or (isinstance(value, float) and pandas.isna(value)):
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", stripped.lower())


def _ftir_category_col(ftir_col):
    """"FTIR (3)" -> "FTIR (3) Drug Category" (the internal name _drugcheck_load gives the
    category column beside each FTIR identification)."""
    return f"{ftir_col} Drug Category"


@lru_cache(maxsize=None)
def load_found_groups(path=CROSSWALK_PATH):
    """The crosswalk, validated: unique group keys (JSON order = display order), every token
    normalized and in at most one group, strip/fallback targets naming real groups + labels."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    groups = data["groups"]
    seen = {}
    for key, group in groups.items():
        if not group.get("label"):
            raise ValueError(f"drugcheck_found_groups: group {key!r} has no label")
        if not group.get("substances"):
            raise ValueError(f"drugcheck_found_groups: group {key!r} lists no substances")
        for label, tokens in group["substances"].items():
            if "|" in label or label in (TOTAL_KEY, ANY_LABEL):
                raise ValueError(f"drugcheck_found_groups: label {label!r} under {key} would collide with "
                                 f"the fact key format (no '|', not {TOTAL_KEY!r} / {ANY_LABEL!r})")
            for token in tokens:
                if token != norm_substance(token):
                    raise ValueError(f"drugcheck_found_groups: {token!r} under {key}/{label} is not normalized")
                if token in seen:
                    raise ValueError(f"drugcheck_found_groups: token {token!r} is in both {seen[token]} and {key}")
                seen[token] = key
    for col, (group, label) in data["strips"].items():
        if col not in STRIP_COLS:
            raise ValueError(f"drugcheck_found_groups: unknown strip column {col!r}")
        if group not in groups or label not in groups[group]["substances"]:
            raise ValueError(f"drugcheck_found_groups: strip {col!r} -> unknown {group}/{label}")
    for token, group in data["category_fallback"].items():
        if token != norm_substance(token) or group not in groups:
            raise ValueError(f"drugcheck_found_groups: bad category_fallback {token!r} -> {group!r}")
    data["ignore"] = set(data.get("ignore", []))
    # token -> (group, label) lookup, built once.
    data["_lookup"] = {token: (key, label)
                       for key, group in groups.items()
                       for label, tokens in group["substances"].items()
                       for token in tokens}
    return data


def group_order():
    """[{"key", "label"}, ...] in display order -- what the cleaner publishes to the client."""
    return [{"key": key, "label": group["label"]} for key, group in load_found_groups()["groups"].items()]


# Tokens the crosswalk didn't list, seen during this process: token -> {raw, category, group}
# (group None = dropped). The cleaner prints these at the end of a run so a new cycle's spellings
# surface in the gen-visuals log instead of silently landing in a fallback bucket.
_UNMAPPED = {}


def unmapped_tokens():
    return dict(_UNMAPPED)


def reset_unmapped():
    _UNMAPPED.clear()


def resolve_ftir(raw_value, raw_category=None):
    """One FTIR cell -> (group_key, label), or None when it identifies nothing. Explicit crosswalk
    entry first; then the ignore list; then the source's own category decides the group and the
    trimmed raw value is the label (recorded in unmapped_tokens); otherwise dropped (recorded)."""
    token = norm_substance(raw_value)
    if not token:
        return None
    data = load_found_groups()
    hit = data["_lookup"].get(token)
    if hit is not None:
        return hit
    if token in data["ignore"]:
        return None
    group = data["category_fallback"].get(norm_substance(raw_category))
    _UNMAPPED[token] = {"raw": str(raw_value).strip(), "category": raw_category, "group": group}
    if group is None:
        return None
    return group, str(raw_value).strip()


METHOD_FTIR = "ftir"
METHOD_STRIP = "strip"


def found_substances_by_method(row):
    """Every (group_key, label, method) detected in one sample row (a mapping of internal column
    name -> cell): FTIR identifications resolved through the crosswalk (method "ftir"), plus each
    strip read Positive (method "strip"). A substance both FTIR and a strip found appears twice."""
    found = set()
    for col in FTIR_COLS:
        resolved = resolve_ftir(row.get(col), row.get(_ftir_category_col(col)))
        if resolved is not None:
            found.add(resolved + (METHOD_FTIR,))
    strips = load_found_groups()["strips"]
    for col, (group, label) in strips.items():
        if norm_substance(row.get(col)) in STRIP_POSITIVE:
            found.add((group, label, METHOD_STRIP))
    return found


def found_substances(row):
    """Every (group_key, label) detected in one sample row, whatever the method (see
    found_substances_by_method)."""
    return {(group, label) for group, label, _ in found_substances_by_method(row)}
