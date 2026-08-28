"""derive_drill_chain: derived state recomputed on every define-visuals, so its mapping
is a contract with both the read path and the client."""
import pytest

from data_viz.visual_definitions import derive_drill_chain


@pytest.mark.parametrize("shape,dim2,expected", [
    ("geo_series", None, ["geo"]),
    ("geo_series", "substance", ["geo", "dimension2"]),
    ("pie_nested", None, ["geo", "time", "dimension2"]),
    ("pie_nested", "substance", ["geo", "time", "dimension2"]),
    ("regional", "result", ["geo", "time", "dimension", "dimension2"]),
    ("flat_series", None, []),
    ("flat_series", "substance", ["dimension2"]),
    ("category_treemap", "drug_category", []),
    ("map_none", None, []),
    ("das_table", None, []),
    (None, None, []),
])
def test_drill_chain(shape, dim2, expected):
    assert derive_drill_chain(shape, dim2) == expected
