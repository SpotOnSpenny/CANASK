###########################################################################################
#                     Visual definitions: JSON manifests -> DB (authoring)                 #
# sync_visual_definitions() loads app_config/visuals/*.json (one file per data source) and #
# upserts the Visuals rows that describe each visual -- its data shape/metric/dimensions,   #
# series-key encoding, chart type, menu placement, and drill links. This is the authoring   #
# half of the V1 pipeline: definitions live in the DB, so the data generator                #
# (generate_visuals.export_data_to_db) reads them straight off each Visuals row instead of  #
# a hard-coded Python registry, and the serve path already reads them too.                  #
#                                                                                           #
# Ownership split (no drift): manifests own definition fields; the Data Ownership UI owns    #
# access fields (visibility + GroupVisuals grants). Rows are upserted in place (id is        #
# stable), so visibility/grants persist across re-syncs untouched; only a *pruned* visual    #
# loses them.                                                                                #
###########################################################################################

import glob
import json
import os

from data_viz.database import db
from data_viz.database.models import DataSources, Visuals, VisualQuery, GroupVisuals, UserActivity

# app_config/visuals/ lives at the project root (one dir above the data_viz package).
MANIFEST_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_config", "visuals")


def derive_drill_chain(shape, dimension2_type):
    """The dimension nesting order a visual drills through, implied by its shape and whether it
    carries a second disaggregator. Matches the chains the read path renders."""
    if shape == "geo_series":
        return ["geo"] + (["dimension2"] if dimension2_type else [])
    if shape == "pie_nested":
        return ["geo", "time", "dimension2"]
    if shape == "regional":
        return ["geo", "time", "dimension", "dimension2"]
    if shape == "flat_series":
        return ["dimension2"] if dimension2_type else []
    if shape == "found_substance_series":
        return []   # expected-drug / group / geo pickers are all client-side (self-contained renderer)
    # Self-contained shapes with no inter-visual drill chain: category_treemap (its
    # category->leaf nesting and geo/time/dimension filters are all client-side),
    # expected_actual_bar (segments and time filter are client-side), and map_none.
    # A NEW shape landing here should be a deliberate choice, not a fallthrough.
    return []


TIME_GRAINS = ("year", "quarter", "month")
SWITCHABLE_CHART_TYPES = ("heatmap", "line")
# The heatmap's Trend view renders through createVisualLine, which reads these for each data type it
# can show ("{dt}" = counts / rates).
TREND_VIEW_OPTIONS = ("{dt}-title", "{dt}-y-axis-title", "table-{dt}-row")
# Measures the heatmap renderer can draw (it labels the colorbar / hover per measure). Its map title is
# `heatmap-title` for counts and `heatmap-<dt>-title` for any other measure.
HEATMAP_DATA_TYPES = ("counts", "rates")


def visual_options_problems(entry):
    """What's wrong with a manifest entry's grain / chart-type / heatmap-measure options ([] = fine).
    They are read by the client and Confluence, which would otherwise disagree about a malformed value
    (a string `time_grains` is a substring test on the server, ignored by the client) or crash on it (a
    Trend view or rates map without its title options)."""
    options = entry.get("visual_options") or {}
    data_types = entry.get("data_types") or []
    problems = []
    grains = options.get("time_grains")
    if grains is not None and (not isinstance(grains, list) or not grains
                               or any(g not in TIME_GRAINS for g in grains)):
        problems.append(f"time_grains must be a non-empty list drawn from {list(TIME_GRAINS)}, got {grains!r}")
    default_grain = options.get("default_grain")
    if default_grain is not None and (not isinstance(grains, list) or default_grain not in grains):
        problems.append(f"default_grain {default_grain!r} must be one of the declared time_grains {grains!r}")
    chart_types = options.get("chart_types")
    if chart_types is not None:
        if not isinstance(chart_types, list) or any(t not in SWITCHABLE_CHART_TYPES for t in chart_types):
            problems.append(f"chart_types must be a list drawn from {list(SWITCHABLE_CHART_TYPES)}, "
                            f"got {chart_types!r}")
        elif entry.get("chart_type") not in chart_types:
            problems.append(f"chart_types {chart_types} must include the entry's chart_type "
                            f"{entry.get('chart_type')!r}")
        elif entry.get("shape") != "geo_series" or entry.get("level") != 1:
            problems.append("chart_types is only supported on a level-1 geo_series visual")
        elif "line" in chart_types:
            missing = [key.format(dt=dt) for dt in data_types or ["counts"] for key in TREND_VIEW_OPTIONS
                       if not options.get(key.format(dt=dt))]
            if missing:
                problems.append(f"a Trend view (chart_types 'line') needs visual_options {missing}")
    if entry.get("chart_type") == "heatmap":
        unsupported = [dt for dt in data_types if dt not in HEATMAP_DATA_TYPES]
        if unsupported:
            problems.append(f"a heatmap's data_types must be drawn from {list(HEATMAP_DATA_TYPES)}, "
                            f"got {unsupported}")
        missing = [f"heatmap-{dt}-title" for dt in data_types
                   if dt in HEATMAP_DATA_TYPES and dt != "counts" and not options.get(f"heatmap-{dt}-title")]
        if missing:
            problems.append(f"a heatmap's non-count measures need their map titles: visual_options {missing}")
    return problems


DATA_SOURCE_KEYS = ("name", "link", "restricted")


def data_source_problems(data_source):
    """What's wrong with a manifest's `data_source` block ([] = fine; None = the sourceless
    scaffolding manifest). Strict on purpose: `restricted` is an access-control switch (see
    data_viz/auth/restricted.py), so a misspelled key or a truthy non-boolean must fail the sync
    rather than quietly leave a source open."""
    if data_source is None:
        return []
    if not isinstance(data_source, dict):
        return [f"data_source must be an object, got {data_source!r}"]
    problems = []
    if not isinstance(data_source.get("name"), str) or not data_source["name"].strip():
        problems.append("data_source needs a non-empty name")
    unknown = sorted(set(data_source) - set(DATA_SOURCE_KEYS))
    if unknown:
        problems.append(f"unknown data_source keys {unknown} (allowed: {list(DATA_SOURCE_KEYS)})")
    if "restricted" in data_source and not isinstance(data_source["restricted"], bool):
        problems.append(f"data_source restricted must be true or false, got {data_source['restricted']!r}")
    return problems


def sync_visual_definitions(manifest_dir=None, prune=True):
    """Upsert every visual defined in the manifests into the Visuals table, keyed by
    (province, visual_id). Returns {"created", "updated", "pruned"} counts.

    Each manifest is the full desired state for its data source: when `prune` is True, Visuals
    linked to that source (sourceless visuals for the source-less scaffolding manifest) that are
    no longer listed are deleted along with their VisualQuery / GroupVisuals children."""
    manifest_dir = manifest_dir or MANIFEST_DIR
    files = sorted(glob.glob(os.path.join(manifest_dir, "*.json")))
    if not files:
        print(f"No visual manifests found in {manifest_dir}.")
        return {"created": 0, "updated": 0, "pruned": 0}

    existing = {(v.province, v.name): v for v in Visuals.query.all()}
    created = updated = pruned = 0
    scopes = []   # (source_id, desired {(province, name)}) per file, for the prune pass
    declared_restricted = {}   # source name -> its `restricted` declaration, to catch two files disagreeing

    try:
        for path in files:
            with open(path) as handle:
                manifest = json.load(handle)
            data_source = manifest.get("data_source")
            problems = data_source_problems(data_source)
            if data_source and not problems:
                restricted = data_source.get("restricted", False)
                if declared_restricted.setdefault(data_source["name"], restricted) != restricted:
                    problems.append(f"manifests disagree on whether {data_source['name']!r} is restricted")
            if problems:
                raise ValueError(f"{os.path.basename(path)}: " + "; ".join(problems))
            source_id = _upsert_source(data_source)
            desired = set()
            for entry in manifest["visuals"]:
                key = (entry["province"], entry["visual_id"])
                problems = visual_options_problems(entry)
                if problems:
                    raise ValueError(f"{os.path.basename(path)} {key[0]}/{key[1]}: " + "; ".join(problems))
                desired.add(key)
                visual = existing.get(key)
                if visual is None:
                    visual = Visuals(name=entry["visual_id"], province=entry["province"],
                                     vis_type=entry["shape"])
                    # New visual: sourceless scaffolding is public (no data to protect); data-bearing
                    # visuals default to private until an owner opts in. Existing rows keep their
                    # owner-set visibility (we never touch it on update).
                    visual.visibility = "public" if source_id is None else "private"
                    # is_default is owner-managed like visibility: seed the landing visual from the
                    # manifest on create, then leave it to the Data Ownership UI (never touched on update).
                    visual.is_default = bool(entry.get("is_default", False))
                    db.session.add(visual)
                    existing[key] = visual
                    created += 1
                else:
                    updated += 1
                _apply_definition(visual, entry, source_id)
            db.session.flush()
            scopes.append((source_id, desired))

        # Drill-heading maps with no source of their own inherit it from their drill child, so the whole
        # L1(map) -> L2 -> L3 chain is owned/restrictable as a unit (standalone maps stay ungated).
        _inherit_map_sources()
        db.session.flush()

        if prune:
            pruned = _prune(scopes)

        db.session.commit()
    except Exception:
        # A mid-loop manifest error leaves flushed-but-uncommitted rows in the session; roll back so a
        # retry or the next session user doesn't inherit partial state, then re-raise to fail loudly.
        db.session.rollback()
        raise
    return {"created": created, "updated": updated, "pruned": pruned}


def _upsert_source(data_source):
    """Upsert a DataSources row by name; return its id (None for the sourceless scaffolding file).
    `link` and `restricted` are set here -- about / scrape-date strings stay owned by gen-visuals.

    `restricted` (data sharing agreement, see data_viz/auth/restricted.py) is owned by the manifest
    in BOTH directions: absent means unrestricted. There is deliberately no other way to set it, so
    lifting a restriction is a reviewed code change plus a deploy. Because rows are keyed by name, a
    renamed source gets a NEW row -- which starts restricted too, since the flag travels with the
    manifest rather than with the old row. Every change is printed and audited."""
    if not data_source:
        return None
    source = DataSources.query.filter_by(name=data_source["name"]).first()
    if source is None:
        source = DataSources(name=data_source["name"], restricted=False)
        db.session.add(source)
    if data_source.get("link") is not None:
        source.link = data_source["link"]
    restricted = data_source.get("restricted", False)
    changed = bool(source.restricted) != restricted
    source.restricted = restricted
    db.session.flush()
    if changed:
        state = "restricted" if restricted else "NO LONGER restricted"
        print(f"Data source {source.name!r} is now {state} (set by its manifest).")
        db.session.add(UserActivity(
            user_id=None,
            activity_type="source_restriction_changed",
            activity_target_type="data_source",
            activity_target_id=source.id,
            details=f"Data source {source.name} is now {state} (define-visuals, from its manifest)."))
    return source.id


def _apply_definition(visual, entry, source_id):
    """Write a manifest entry's definition + menu fields onto a Visuals row (visibility and
    is_default excluded -- both are UI-owned, seeded once on create)."""
    shape = entry["shape"]
    dim2 = entry.get("dimension2_type")
    visual.province = entry["province"]
    visual.name = entry["visual_id"]
    # Deep-link URL slug: explicit manifest override, else auto-derived from the (per-province-unique)
    # visual_id so every visual is addressable without mandatory manifest edits.
    visual.slug = entry.get("slug") or entry["visual_id"].replace("_", "-").lower()
    visual.vis_type = shape
    visual.data_shape = shape
    visual.data_source_id = source_id
    # Self-describing query definition (read by both the serve path and gen-visuals).
    visual.metric = entry.get("metric")
    visual.geo_type = entry.get("geo_type") or ("province" if shape == "flat_series" else None)
    visual.dimension_type = entry.get("dimension_type")
    visual.dimension2_type = dim2
    visual.key_kind = entry.get("key_kind")
    visual.drill_chain = derive_drill_chain(shape, dim2)
    # Generic per-visual presentation/stratifier config (served as-is by _base_block). Two authoring
    # sources: config-driven visuals (e.g. category_treemap) declare it in the manifest, while
    # line/bar/pie/heatmap visuals get their titles/axis/table labels from the cleaned block at
    # gen-visuals time. Only overwrite from the manifest when it actually provides one, so re-syncing
    # definitions (e.g. a slug change) never wipes the gen-visuals block-derived options.
    if entry.get("visual_options") is not None:
        visual.visual_options = entry["visual_options"]
    # Menu / presentation config (served to the frontend; formerly the static visuals.js).
    visual.chart_type = entry.get("chart_type")
    data_types = entry.get("data_types")
    # Comma-joined for storage; drop empty/whitespace members so the split on the read path never
    # yields a stray "" that would flow to the client as a phantom data type.
    cleaned_types = [dt for dt in (data_types or []) if dt and dt.strip()]
    visual.data_types = ",".join(cleaned_types) if cleaned_types else None
    visual.menu_name = entry.get("menu_name")
    visual.menu_parent = entry.get("menu_parent")
    level = entry.get("level")
    visual.level = str(level) if level is not None else None
    visual.vis_parent_name = entry.get("vis_parent")
    visual.next_vis_name = entry.get("next_vis")


def _inherit_map_sources():
    for visual in Visuals.query.filter_by(data_shape="map_none").all():
        if not (visual.next_vis_name and visual.data_source_id is None):
            continue
        # Walk the drill chain until a descendant carries a source: a sourceless map may point at
        # another sourceless map (a map-to-map heading) with the real data two+ levels down, so the
        # immediate child isn't always the one that knows the source.
        child = Visuals.query.filter_by(province=visual.province, name=visual.next_vis_name).first()
        seen = {visual.name}
        while child is not None and child.name not in seen:
            if child.data_source_id is not None:
                visual.data_source_id = child.data_source_id
                break
            seen.add(child.name)
            if not child.next_vis_name:
                break
            child = Visuals.query.filter_by(province=visual.province, name=child.next_vis_name).first()


def _prune(scopes):
    """Delete Visuals (and their VisualQuery / GroupVisuals children) within each file's scope -- its
    data source, or the sourceless set for the scaffolding file -- that the manifest no longer lists."""
    pruned = 0
    for source_id, desired in scopes:
        if source_id is None:
            rows = Visuals.query.filter(Visuals.data_source_id.is_(None)).all()
        else:
            rows = Visuals.query.filter(Visuals.data_source_id == source_id).all()
        for visual in rows:
            if (visual.province, visual.name) in desired:
                continue
            VisualQuery.query.filter_by(for_visual_id=visual.id).delete()
            GroupVisuals.query.filter_by(visual_id=visual.id).delete()
            db.session.delete(visual)
            pruned += 1
    return pruned
