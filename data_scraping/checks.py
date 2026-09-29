# Two-tier validation for scraped/uploaded source files.
#   Tier 1 (structure): missing required sheets/columns or unparseable typed columns REJECT; extra
#     sheets/columns and unmapped dimension values PASS with a "new data available" notice.
#   Tier 2 (vs the active version): decides publish vs hold vs no-new-data.

import difflib
import hashlib
import os
from dataclasses import dataclass

import pandas

from data_scraping.contracts import read_frames

_NUMERIC_OK_RATIO = 0.95   # share of non-null, non-marker cells that must parse in a typed column
_MAX_LISTED = 20


@dataclass
class CheckResult:
    check: str
    level: str   # pass | warn | fail | notice
    message: str

    def to_dict(self):
        return {"check": self.check, "level": self.level, "message": self.message}


@dataclass
class Tier1Outcome:
    ok: bool
    results: list


@dataclass
class ActiveVersion:
    path: str
    data_until: object
    content_hash: str


@dataclass
class Tier2Outcome:
    decision: str   # ok | hold | no_new_data
    results: list


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _listed(values):
    values = sorted(map(str, values))
    more = f" (+{len(values) - _MAX_LISTED} more)" if len(values) > _MAX_LISTED else ""
    return ", ".join(values[:_MAX_LISTED]) + more


def _label(sheet):
    return "file" if sheet is None else f"sheet '{sheet}'"


def _kind_problem(series, kind, markers):
    values = series.dropna()
    if kind in ("any", "text") or values.empty:
        return None
    values = values[~values.astype(str).str.strip().isin(markers)]
    if values.empty:
        return None
    if kind == "number":
        parsed = pandas.to_numeric(values, errors="coerce")
    elif kind == "date":
        parsed = pandas.to_datetime(values, errors="coerce", format="mixed")
    else:
        raise ValueError(f"unknown column kind {kind!r}")
    ratio = parsed.notna().mean()
    if ratio < _NUMERIC_OK_RATIO:
        bad = values[parsed.isna()].astype(str).unique()[:5]
        return f"only {ratio:.0%} of values are {kind}s (e.g. {', '.join(bad)})"
    return None


def tier1(path, contract):
    results = []
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    if ext != contract.ext:
        return Tier1Outcome(False, [CheckResult("file_type", "fail", f"expected a .{contract.ext} file, got .{ext}")])
    if contract.custom:
        try:
            contract.custom(path)
        except Exception as exc:   # unreadable/malformed file is a rejection, not a crash
            return Tier1Outcome(False, [CheckResult("structure", "fail", str(exc))])
        return Tier1Outcome(True, [CheckResult("structure", "pass", "workbook parsed")])
    try:
        frames = read_frames(path, contract)
    except Exception as exc:   # unreadable file (corrupt xlsx, not a CSV) is a rejection, not a crash
        return Tier1Outcome(False, [CheckResult("readable", "fail", f"could not read file: {exc}")])

    missing_sheets = [s for s in contract.sheets if s not in frames]
    if missing_sheets:
        results.append(CheckResult("sheets", "fail", f"missing sheet(s): {_listed(missing_sheets)}"))
    extra_sheets = [s for s in frames if s not in contract.sheets]
    if extra_sheets and contract.ext == "xlsx":
        results.append(CheckResult("sheets", "notice", f"new sheet(s) not used yet: {_listed(extra_sheets)}"))

    for name, sheet in contract.sheets.items():
        frame = frames.get(name)
        if frame is None:
            continue
        present = [str(c) for c in frame.columns]
        missing = [c for c in sheet.required if c not in present]
        extra = [c for c in present if c not in sheet.required and not c.startswith("Unnamed")]
        for column in missing:
            hint = difflib.get_close_matches(column, extra, n=1, cutoff=0.6)
            suffix = f" -- did it become '{hint[0]}'?" if hint else ""
            results.append(CheckResult("columns", "fail", f"{_label(name)}: missing column '{column}'{suffix}"))
        if extra:
            results.append(CheckResult("columns", "notice", f"{_label(name)}: new column(s) not used yet: {_listed(extra)}"))
        if len(frame) < sheet.min_rows:
            results.append(CheckResult("rows", "fail", f"{_label(name)}: {len(frame)} rows, expected at least {sheet.min_rows}"))
        for column, kind in sheet.required.items():
            if column in present:
                problem = _kind_problem(frame[column], kind, sheet.markers)
                if problem:
                    results.append(CheckResult("types", "fail", f"{_label(name)}: '{column}' {problem}"))
        for column, known in sheet.known_values.items():
            if column in present:
                seen = set(frame[column].dropna().astype(str).str.strip())
                new = seen - set(known)
                if new:
                    results.append(CheckResult("values", "notice",
                                               f"{_label(name)}: new '{column}' value(s) the cleaner doesn't map yet: {_listed(new)}"))

    ok = not any(r.level == "fail" for r in results)
    if ok and not any(r.level == "notice" for r in results):
        results.append(CheckResult("structure", "pass", "all required sheets and columns present"))
    return Tier1Outcome(ok, results)


def _row_total(frames, contract):
    return sum(len(frames[name]) for name in contract.sheets if name in frames)


def _settled_change_ratio(old, new, rev):
    periods = sorted(old[rev.period_column].dropna().astype(str).unique())
    settled = set(periods[:-rev.recent_periods]) if rev.recent_periods else set(periods)
    if not settled:
        return 0.0
    keys = list(rev.key_columns) + [rev.period_column]
    old_s = old[old[rev.period_column].astype(str).isin(settled)]
    merged = old_s.merge(new, on=keys, how="left", suffixes=("_old", "_new"))
    before = merged[f"{rev.value_column}_old"].astype(str)
    after = merged[f"{rev.value_column}_new"].astype(str)
    return float((before != after).mean()) if len(merged) else 0.0


def tier2(spec, new_path, new_until, new_hash, active):
    results = []
    if active is None:
        return Tier2Outcome("ok", [CheckResult("first_version", "pass", "no active version to compare against")])
    if new_hash == active.content_hash:
        return Tier2Outcome("no_new_data", [CheckResult("content", "pass", "identical to the active version")])
    if new_until and active.data_until and new_until < active.data_until:
        results.append(CheckResult("data_until", "fail",
                                   f"data until {new_until} is earlier than the active version's {active.data_until}"))
    elif new_until == active.data_until:
        results.append(CheckResult("data_until", "warn", "same data-until as the active version, but the content changed"))
    else:
        results.append(CheckResult("data_until", "pass", f"{active.data_until} -> {new_until}"))

    if spec.kind == "v1" and spec.contract and not spec.contract.custom:
        old_frames = read_frames(active.path, spec.contract)
        new_frames = read_frames(new_path, spec.contract)
        old_rows, new_rows = _row_total(old_frames, spec.contract), _row_total(new_frames, spec.contract)
        if old_rows and (old_rows - new_rows) / old_rows * 100 > spec.thresholds.row_drop_pct:
            results.append(CheckResult("row_count", "fail",
                                       f"rows dropped {old_rows} -> {new_rows} (limit {spec.thresholds.row_drop_pct:g}%)"))
        else:
            results.append(CheckResult("row_count", "pass", f"{old_rows} -> {new_rows} rows"))
        for name, sheet in spec.contract.sheets.items():
            if sheet.revision and name in old_frames and name in new_frames:
                ratio = _settled_change_ratio(old_frames[name], new_frames[name], sheet.revision) * 100
                if ratio > spec.thresholds.revision_warn_pct:
                    results.append(CheckResult("settled_revisions", "warn",
                                               f"{_label(name)}: {ratio:.1f}% of settled values changed"))

    decision = "hold" if any(r.level == "fail" for r in results) else "ok"
    return Tier2Outcome(decision, results)
