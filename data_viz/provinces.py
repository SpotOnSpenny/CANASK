# Province constants shared by the write side (generate_visuals) and the serve side (confluence,
# menus). Kept in a dependency-free module so the request path never has to import
# generate_visuals (which pulls pandas) just to map a URL slug to a display name or postal code.

# URL-friendly province slug -> the display/Region name used as the geo for province-level facts.
PROVINCE_LABELS = {
    "british-columbia": "British Columbia",
    "alberta": "Alberta",
    "saskatchewan": "Saskatchewan",
    "manitoba": "Manitoba",
    "new-brunswick": "New Brunswick",
    "ontario": "Ontario",
    "nova-scotia": "Nova Scotia",
    "quebec": "Quebec",
    "prince-edward-island": "Prince Edward Island",
    "newfoundland-and-labrador": "Newfoundland and Labrador",
    "yukon": "Yukon",
    "northwest-territories": "Northwest Territories",
    "nunavut": "Nunavut",
}

# Slug -> two-letter postal abbreviation: the form the DAS tables store in `province` and the
# gazetteer uses in its "City, PR" keys.
PROVINCE_CODES = {
    "british-columbia": "BC",
    "alberta": "AB",
    "saskatchewan": "SK",
    "manitoba": "MB",
    "new-brunswick": "NB",
    "ontario": "ON",
    "nova-scotia": "NS",
    "quebec": "QC",
    "prince-edward-island": "PE",
    "newfoundland-and-labrador": "NL",
    "yukon": "YT",
    "northwest-territories": "NT",
    "nunavut": "NU",
}
