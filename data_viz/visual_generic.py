###########################################################################################
#                          Generic (fact-based) read path                                 #
# build_province_generic() is the V1 data read path: it selects each visual's normalized   #
# facts straight from its self-describing Visuals columns (metric / geo_type / dimension* / #
# key_kind / drill_chain) and returns {metadata, facts}. The frontend adapts these facts to #
# Plotly client-side (data_viz/static/js/visualGeneration.js: genericToLegacy).             #
###########################################################################################

from data_viz.database.models import DataPoints
from data_viz.visual_query import allowed_visuals, _predicates, _base_block, _value

# geo_type marking a province-level (flat) fact; province-level visuals share source+metric across
# provinces, so their facts are additionally scoped by geo (the province display name).
PROVINCE_GEO_TYPE = "province"


def build_province_generic(province, user=None):
    """Column-driven generic payload: {visual_id: {chart_type, shape, data_types, geo_type,
    drill_chain, data_source?, visual_options?, facts: [{dt, geo, t, d, d2, v}, ...]}}."""
    payload = {}
    for visual in allowed_visuals(user, province):
        block = _base_block(visual)   # data_source (non-map) + visual_options
        block.update({
            "chart_type": visual.chart_type,
            "shape": visual.data_shape,
            "data_types": visual.data_types.split(",") if visual.data_types else None,
            "geo_type": visual.geo_type,
            "drill_chain": visual.drill_chain,
            "key_kind": visual.key_kind,
            "facts": _visual_facts(visual),
        })
        payload[visual.name] = block
    return payload


def _visual_facts(visual):
    """Select a visual's DataPoints using only its self-describing columns (+ the VisualQuery geo /
    additional-metric predicates), and return them as plain fact dicts."""
    if not visual.metric:
        return []   # map_none: no data
    preds = _predicates(visual.id)
    facts = []

    main = DataPoints.query.filter_by(data_source_id=visual.data_source_id, data_metric=visual.metric)
    if visual.geo_type:
        main = main.filter_by(geo_type=visual.geo_type)
    if visual.dimension2_type is not None:
        main = main.filter(DataPoints.dimension2_type == visual.dimension2_type)
    else:
        main = main.filter(DataPoints.dimension2_type.is_(None))
    # Province-level (flat) visuals share source+metric across provinces -> scope to this geo.
    if visual.geo_type == PROVINCE_GEO_TYPE and preds.get("geo"):
        main = main.filter(DataPoints.geo == preds["geo"][0])
    facts.extend(_as_fact(p) for p in main.all())

    add_metrics = preds.get("additional_metric", [])
    if add_metrics:
        add = DataPoints.query.filter(
            DataPoints.data_source_id == visual.data_source_id,
            DataPoints.data_type == "additional_rows",
            DataPoints.data_metric.in_(add_metrics))
        if visual.geo_type:
            add = add.filter_by(geo_type=visual.geo_type)
        if visual.geo_type == PROVINCE_GEO_TYPE and preds.get("geo"):
            add = add.filter(DataPoints.geo == preds["geo"][0])
        facts.extend(_as_fact(p) for p in add.all())
    return facts


def _as_fact(p):
    return {"dt": p.data_type, "geo": p.geo, "t": p.time_frame,
            "d": p.dimension_value, "d2": p.dimension2_value, "v": _value(p)}


