# File contracts: what a source file must contain for its cleaner to work. Tier-1 validation
# (data_scraping/checks.py) reads files through here so the check sees what the cleaner will see.

from dataclasses import dataclass, field
from typing import Callable

import pandas


@dataclass(frozen=True)
class RevisionConfig:
    period_column: str
    value_column: str
    key_columns: tuple
    recent_periods: int   # the newest N distinct periods are "unsettled"; older ones are compared


@dataclass(frozen=True)
class SheetContract:
    required: dict                       # column -> kind: "text" | "number" | "date" | "any"
    known_values: dict = field(default_factory=dict)   # column -> frozenset of values the cleaner maps
    markers: frozenset = frozenset()     # non-numeric sentinels allowed in "number" columns ("Suppr.")
    min_rows: int = 1
    header_row: int = 0
    revision: RevisionConfig | None = None


@dataclass(frozen=True)
class FileContract:
    ext: str                              # "csv" | "xlsx"
    sheets: dict = field(default_factory=dict)   # sheet name -> SheetContract; None = the CSV itself
    normalize: Callable | None = None     # frame -> frame, applied before column checks (header renames)
    custom: Callable | None = None        # path -> None, raises ValueError on a structural problem (DAS)


def read_frames(path, contract):
    if contract.ext == "csv":
        sheet = contract.sheets.get(None)
        frames = {None: pandas.read_csv(path, header=sheet.header_row if sheet else 0)}
    else:
        frames = {}
        for name in pandas.ExcelFile(path, engine="calamine").sheet_names:
            sheet = contract.sheets.get(name)
            frames[name] = pandas.read_excel(path, engine="calamine", sheet_name=name,
                                             header=sheet.header_row if sheet else 0)
    if contract.normalize:
        frames = {name: contract.normalize(frame) for name, frame in frames.items()}
    return frames
