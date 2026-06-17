# CANASK — DB-backed V1 visuals

Notes on the architecture that moved V1 visuals from a static JSON file to the database, the data
model, and how to extend it. (Branch where this landed: `db-backed-visuals`.)

---

## 1. What changed and why

**Before:** `generateVisuals.py` scraped + cleaned data and wrote one big precomputed file,
`data_viz/static/js/visual_data.json`. The browser fetched that static file and built Plotly charts
from it. The DB was not involved; the file was identical for every user (no per-visual gating
possible), and it duplicated data already destined for the (unused) DB scaffolding tables.

**After:** the same cleaning code now **persists into the database** as normalized rows, and the
frontend **queries the DB** through a route that reconstructs the exact JSON shape per request.

Two decoupled steps (they are *not* a per-request round-trip):

| Step | Trigger | Reads from | Writes to |
|------|---------|-----------|-----------|
| **Persistence** `export_data_to_db()` | `make gen-visuals` (occasionally, after scraping) | scraped `output/*.xlsx/.csv` via the `v1_*_export_clean()` builders | DB (`DataSources`, `Visuals`, `DataPoints`, `VisualQuery`) |
| **Reconstruction** `build_province_payload()` | every page load, via `/api/v1/province/<p>/data` | DB | JSON response (no file) |

Nothing in the serve path reads `visual_data.json` anymore. The old `export_data_json()` and the
`visual_data.json` file are orphaned (only the `__main__` block calls the former).

---

## 2. The data model (the important part)

Each `DataPoints` row is a **star-schema fact**:

```
data_metric   = the EVENT being measured  ->  deaths | samples | strip_positive |
                spectrometer_positive | spectrometer_opioid_positive | total_*
data_type     = the unit                  ->  counts | rates | percentages | additional_rows
geo / geo_type                            ->  Alberta / province ,  Fraser / health_authority
time_frame / time_frame_type              ->  "2022" / year
dimension_type  / dimension_value         ->  substance / opioids   (generic slot 1)
dimension2_type / dimension2_value        ->  age_group / 20-29      (generic slot 2)
data_value     = numeric value (queryable, nullable)
data_value_text= original string cell, if any (round-trips exact JSON type)
```

**Design rule ("Layout A"):** `data_metric` is the reusable *event*, and the substance + the specific
disaggregator are the two **dimensions** — NOT baked into a compound metric name. So:

```
Alberta 2022 Fentanyl opioid deaths = 150  ->
  data_metric=deaths  dimension=(substance: opioids)  dimension2=(drug_type: Fentanyl)
  geo=Alberta  data_type=counts  data_value=150
```

This makes facts queryable across visuals, e.g.:
```sql
SELECT * FROM data_points
WHERE data_metric='deaths' AND data_type='counts'
  AND geo='Alberta' AND dimension_type='age_group' AND dimension_value='20-29';
```

`data_value_text` exists because the source mixes string cells (`"47"`) and real numbers; storing the
string verbatim lets reconstruction reproduce the original JSON type. (Frontend-safe fidelity bar:
Plotly/JS treat `2016` and `"2016"` identically, so numeric-string vs number is not a real difference.)

---

## 3. Components / files

| File | Role |
|------|------|
| `data_viz/database/models.py` | Schema. `DataPoints` (facts), `DataSources` (+ `about`, `last_updated_str`, `data_until_str`), `Visuals` (+ `data_source_id`, `visual_options` JSON, `data_shape`), `VisualQuery` (link predicates). |
| `data_viz/visual_specs.py` | **`VISUAL_SPECS`** registry + `encode_series_key`/`decode_series_key`. Pure Python, no DB/Flask imports. Shared by write + read sides so the encode/decode can't drift. |
| `data_viz/generateVisuals.py` | `export_data_to_db(data=None)` — walks each cleaned block per its spec and writes rows. `data=` can inject already-cleaned dicts (used for verification without scraping). Cleaning functions are untouched. |
| `data_viz/visual_query.py` | `build_province_payload(province)` — queries the DB and rebuilds the exact block shapes (inverse of persistence). No file reads. |
| `data_viz/main.py` | `GET /api/v1/province/<province>/data` (`@require_auth`) → `jsonify(build_province_payload(...))`. |
| `data_viz/cli.py` | `flask gen-visuals` → `export_data_to_db()`. |
| `data_viz/static/js/visualGeneration.js` | Fetches `/api/v1/province/${province}/data` (was `visual_data.json`); `currentData = dataJson` (was `dataJson[province]`). |
| `data_viz/static/js/visuals.js` | **Still static.** Menu + chart `type`/`level`/`data-types`/menu config, read client-side. NOT in the DB. |

Migration: `data_viz/database/migrations/versions/7e72cf29af2e_*` (adds the columns above). Generated
with `flask db migrate`, applied with `flask db upgrade` / `make migrate`.

---

## 4. The five data shapes

`VISUAL_SPECS[visual_id]["shape"]` discriminates how a block is encoded/decoded:

| shape | block structure | example visuals |
|-------|-----------------|-----------------|
| `flat_series` | `data[dtype] = {x:[years], "<key>":[...]}` (single geo = province) | national deaths_by_age/sex/manner/drug_type, BC drug_supply, fent_benz |
| `geo_series` | `data[dtype][geo] = {x:[years], "<key>":[...]}` | drug_death_heatmap, BC deaths_by_sex_line |
| `pie_nested` | `data["counts"][ha][year][drug] = int` + `tabular_data` | geographical_drug_supply_pie |
| `regional` | `data["counts"][ha][year][drug] = {"<result>_y":[count]}` | regional_drug_supply_breakdown |
| `map_none` | `visual_options` only, no data, no data_source | drug_supply_geographically |

**Series-key templates** (`key` field) bridge the legacy JSON keys and the dimension values:

| `key` | encodes/decodes | example |
|-------|-----------------|---------|
| `constant` | single series, fixed key (`key_constant`) | `"y"` (heatmap) |
| `suffix_y` | `dimension2 = key without "_y"` | `"20-29_y"` ⇄ `20-29` |
| `plain` | `dimension2 = key` (no suffix) | `"Fentanyl"` |
| `sex_substance` | `"<sex> <Substance>_y"` ⇄ sex (dim2) + substance (dim1) | `"Male Opioid_y"` |
| `manner_substance` | `"<manner> <Substance> Deaths"` ⇄ manner + substance | `"Accidental Opioid Deaths"` |

`substance` field controls the substance dimension: `None`, `"opioids"` (constant), `"from_key"`
(parsed out of the key, for sex/manner), or `"lookup"` (from a `{drug_type: substance}` map built off
the national data, used by `deaths_by_drug_type`).

Two special cases worth remembering:
- **regional empty cells**: a `(ha, year, drug)` with no spectrometer results is an empty `{}` leaf the
  frontend drills into; those produce no `DataPoints`, so reconstruction rebuilds the full grid by
  reusing the **pie visual's** `(ha, year, drug)` grid (`grid_from` in the spec) and fills empties.
- **regional `rates`**: the source builds an always-empty `{ha:{year:{}}}` and the JS never reads it
  (`data-types: ["counts"]`); reconstruction reproduces the empty skeleton. Also emits `tabular_data: {}`.

---

## 5. How `VisualQuery` links a visual to its facts

There is **no `visual_id` on `DataPoints`** (deliberate). The link is expressed as query predicates in
`VisualQuery` (`filter_type`, `filter_value`, `for_visual_id`), written per visual during persistence:
`source`, `geo_type`, `geo` (flat), `metric`, `dimension_type`/`dimension2_type` (to disambiguate
visuals that share a metric — e.g. national sex vs age both use `deaths`), and `additional_metric` per
total row. Reconstruction reads these predicates to build the `DataPoints` query, then `VISUAL_SPECS`
supplies the shape + key templates to rebuild the JSON.

---

## 6. Adding a new V1 visual (steady-state checklist)

For a visual that fits an existing shape + chart type, author **three** things; the DB rows are
generated:

**1. Data block** — in `generateVisuals.py`, build the block and include it in the province dict:
```python
new_visual = {
    "data_source": {"name": "...", "about": "...", "link": "...",
                    "last_updated": last_updated, "data_until": data_until},
    "data": {"counts": {"x": years, "High School_y": [...], "Post-Secondary_y": [...]},
             "percentages": {...}},
    "visual_options": {"counts-title": "...", ...},
    # "additional_rows": {"Total Deaths": [...]},   # optional table-only rows
}
# ... include in the returned dict, e.g. ab_data["deaths_by_education"] = new_visual
```

**2. `VISUAL_SPECS` entry** — in `visual_specs.py`, keyed by the visual_id (REQUIRED, or persistence
KeyErrors):
```python
"deaths_by_education": {
    "shape": "flat_series",
    "metric": "deaths",                 # reuse the semantic event
    "dimension2_type": "education_level",
    "substance": "opioids",             # or None / "from_key" / "lookup"
    "key": "suffix_y",                  # series keys look like "High School_y"
},
```

**3. `visuals.js` entry** — under the province (drives the menu + chart type, still static):
```js
"deaths_by_education": {
    "type": "line",                     // line | bar | heatmap | pie | map
    "data-types": ["counts", "percentages"],
    "menu-parent": "Deaths and Demographics",
    "menu-name": "Opioid Deaths by Education",
    "level": 1, "vis-parent": null, "next-vis": null
},
```

**4. Run** `make gen-visuals`. `export_data_to_db()` auto-creates the `DataSources`, `Visuals`,
`DataPoints`, and `VisualQuery` rows. **No migration** (data, not schema). No hand-written SQL.

You do NOT manually insert DataPoints or VisualQuery rows — those are outputs of persistence.

### When you must touch the engine
- **New data shape** (not one of the five) → add a branch in BOTH `export_data_to_db` and
  `build_province_payload`, and a `shape` value in the spec.
- **New series-key format** → add a `key` kind to `encode_series_key` + `decode_series_key`.
- **New chart type** (not line/bar/heatmap/pie/map) → add a `createVisual*` renderer and a `case` in
  `masterLoop` in `visualGeneration.js`.

---

## 7. Verification approach

Because the scraped `output/` files are gitignored / not on disk, the round-trip was verified by
injecting the golden `visual_data.json` as the cleaned dicts (`export_data_to_db(data=golden)`), then
diffing `build_province_payload(province)` against the golden slice for every province. Result: **0
semantic mismatches across all 6 provinces**, including the hard BC pie/regional visuals. The injection
path (`data=` parameter) exists solely for this; live `gen-visuals` passes nothing and builds from the
scrapers.

To repopulate a fresh DB for UI testing without scrape files, either drop the files into `output/` and
run `make gen-visuals`, or re-inject the golden file via a short script using `export_data_to_db(data=...)`.

---

## 8. Future: per-visual restriction

Enforce in the **fetch** (the single server-side chokepoint), since the client can't be trusted:
filter `Visuals.query.filter_by(province=...)` in `build_province_payload` down to the user's allowed
set. Each `Visuals` row carries `data_source_id`, so the natural mechanism is the already-active
`GroupDataSources` (a user's groups grant data sources → they see those sources' visuals); the unused
`GroupVisuals` table is there if you need finer per-visual grants. Pair the server gate with **menu
filtering**: have the frontend build its menu only from the keys present in the fetched payload
(`intersect visuals[province] with Object.keys(currentData)`) so menu and data stay in lockstep.

---

## 9. Gotchas / notes

- **Ragged series**: some series are shorter than the dtype's `x` (e.g. a stimulant series with fewer
  years than opioids). Persistence stores only the years a series has; reconstruction emits each series
  at its own length (does NOT pad to the union). Don't "fix" this to pad — it would diverge from source.
- **x-axis int vs string**: the source is internally inconsistent (e.g. SK rates `x` is ints, counts `x`
  is strings). The schema stores `time_frame` as a string, so reconstruction emits strings. Harmless —
  Plotly renders identically.
- **`output/` empty by default**: `gen-visuals` (live) `FileNotFoundError`s without the scrape files.
- **Orphaned legacy**: `export_data_json()` + `static/js/visual_data.json` are unused; safe to delete.
- **No Celery wiring yet**: nothing schedules `gen-visuals` after a scrape — that's a clean follow-up
  (register a task in `celery_worker/celery.py`'s `imports` and call `export_data_to_db()`).
