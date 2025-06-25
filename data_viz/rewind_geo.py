import geojson_rewind
import json

# Load and rewind your GeoJSON
with open("data_viz/static/assets/geojsons/british-columbia.geojson", "r") as f:
    data = json.load(f)

# Rewind with the "legacy" (non-RFC 7946) order
rewound = geojson_rewind.rewind(data, rfc7946=False)

# Save back (optional)
with open("bc-rewound.geojson", "w") as f:
    json.dump(rewound, f)
