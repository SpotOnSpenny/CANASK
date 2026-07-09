###########################################################################################
#                          Generic (fact-based) read path                                 #
# build_province_generic() is the V1 fact read path: it selects a visual's DataPoints using  #
# only the columns that actually filter (metric / geo_type / dimension2_type) and returns    #
# {metadata, facts}. key_kind / drill_chain are NOT selectors -- they ride along in each      #
# block for the client, which adapts the facts to Plotly in static/js/visualGeneration.js.    #
###########################################################################################

from data_viz.database.models import DataPoints
from data_viz.visual_query import displayable_visuals, _predicates, _base_block, _value

# geo_type marking a province-level (flat) fact; province-level visuals share source+metric across
# provinces, so their facts are additionally scoped by geo (the province display name).
PROVINCE_GEO_TYPE = "province"


def build_province_generic(province, user=None):
    """Column-driven generic payload: {visual_id: {chart_type, shape, data_types, geo_type,
    drill_chain, data_source?, visual_options?, facts: [{dt, geo, t, d, d2, v}, ...]}}."""
    payload = {}
    for visual in displayable_visuals(user, province):
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

    # Province-level (flat) visuals share source+metric+dimension2_type across ALL provinces, so they
    # are meaningful only when scoped to this province's geo. The write path always emits a "geo"
    # predicate alongside such a visual's facts, so a *missing* geo predicate means this province has
    # no data for the visual (e.g. a territory whose breakdown is privacy-suppressed) -- return empty
    # rather than leaking every other province's facts.
    if visual.geo_type == PROVINCE_GEO_TYPE and not preds.get("geo"):
        return []

    main = DataPoints.query.filter_by(data_source_id=visual.data_source_id, data_metric=visual.metric)
    if visual.geo_type:
        main = main.filter_by(geo_type=visual.geo_type)
    if visual.dimension2_type is not None:
        main = main.filter(DataPoints.dimension2_type == visual.dimension2_type)
    else:
        main = main.filter(DataPoints.dimension2_type.is_(None))
    # When the write path recorded the dimension values this visual owns (a "dimension" predicate per
    # emitted fact), scope to them so two visuals sharing every other selector but differing by dimension
    # value -- e.g. opioid vs stimulant harms-by-type -- don't return each other's facts. NULL-dimension
    # rows belong to no specific slice, so they're always kept.
    dims = preds.get("dimension")
    if dims:
        main = main.filter(DataPoints.dimension_value.in_(dims) | DataPoints.dimension_value.is_(None))
    if visual.geo_type == PROVINCE_GEO_TYPE:
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


