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
from data_viz.database.models import DataSources, Visuals, VisualQuery, GroupVisuals

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
    # category_treemap is self-contained: its category->leaf nesting and its geo/time/dimension
    # filters are all client-side, so there is no inter-visual drill chain.
    return []


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

    for path in files:
        with open(path) as handle:
            manifest = json.load(handle)
        source_id = _upsert_source(manifest.get("data_source"))
        desired = set()
        for entry in manifest["visuals"]:
            key = (entry["province"], entry["visual_id"])
            desired.add(key)
            visual = existing.get(key)
            if visual is None:
                visual = Visuals(name=entry["visual_id"], province=entry["province"],
                                 vis_type=entry["shape"])
                # New visual: sourceless scaffolding is public (no data to protect); data-bearing
                # visuals default to private until an owner opts in. Existing rows keep their
                # owner-set visibility (we never touch it on update).
                visual.visibility = "public" if source_id is None else "private"
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
    return {"created": created, "updated": updated, "pruned": pruned}


def _upsert_source(data_source):
    """Upsert a DataSources row by name; return its id (None for the sourceless scaffolding file).
    Only `link` is set here -- about / scrape-date strings stay owned by gen-visuals."""
    if not data_source:
        return None
    source = DataSources.query.filter_by(name=data_source["name"]).first()
    if source is None:
        source = DataSources(name=data_source["name"])
        db.session.add(source)
    if data_source.get("link") is not None:
        source.link = data_source["link"]
    db.session.flush()
    return source.id


def _apply_definition(visual, entry, source_id):
    """Write a manifest entry's definition + menu fields onto a Visuals row (visibility excluded --
    that is UI-owned)."""
    shape = entry["shape"]
    dim2 = entry.get("dimension2_type")
    visual.province = entry["province"]
    visual.name = entry["visual_id"]
    visual.vis_type = shape
    visual.data_shape = shape
    visual.data_source_id = source_id
    # Self-describing query definition (read by both the serve path and gen-visuals).
    visual.metric = entry.get("metric")
    visual.geo_type = entry.get("geo_type") or ("province" if shape == "flat_series" else None)
    visual.dimension_type = entry.get("dimension_type")
    visual.dimension2_type = dim2
    visual.substance = entry.get("substance")
    visual.key_kind = entry.get("key_kind")
    visual.drill_chain = derive_drill_chain(shape, dim2)
    # Generic per-visual presentation/stratifier config (served as-is by _base_block). Most visuals
    # omit it (kept None, renderers fall back to hardcoded titles); config-driven visuals such as
    # the category_treemap declare their geo levels / hierarchy / filters / time control here.
    visual.visual_options = entry.get("visual_options")
    # Menu / presentation config (served to the frontend; formerly the static visuals.js).
    visual.chart_type = entry.get("chart_type")
    data_types = entry.get("data_types")
    visual.data_types = ",".join(data_types) if data_types else None
    visual.menu_name = entry.get("menu_name")
    visual.menu_parent = entry.get("menu_parent")
    level = entry.get("level")
    visual.level = str(level) if level is not None else None
    visual.vis_parent_name = entry.get("vis_parent")
    visual.next_vis_name = entry.get("next_vis")
    visual.is_default = bool(entry.get("is_default", False))


def _inherit_map_sources():
    for visual in Visuals.query.filter_by(data_shape="map_none").all():
        if visual.next_vis_name and visual.data_source_id is None:
            child = Visuals.query.filter_by(province=visual.province, name=visual.next_vis_name).first()
            if child and child.data_source_id is not None:
                visual.data_source_id = child.data_source_id


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
