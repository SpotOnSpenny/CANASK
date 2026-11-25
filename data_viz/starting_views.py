###########################################################################################
#                Saved "starting views" for pages whose view lives in the address          #
# Confluence and the DAS Explorer have no list of charts to pick a starting chart from:    #
# what they show is a set of settings carried in the query string (a deep link). A user    #
# can save one such set per page; the page applies it when opened with an empty address.   #
# This module only knows the pages and how to reduce a pasted link / query string to a     #
# page's recognised settings. Pure: no models, no request.                                 #
###########################################################################################

from urllib.parse import parse_qsl, urlencode, urlsplit

# page key -> label, path, and the settings its deep link may carry: exact `keys`, plus any key
# starting with one of `prefixes` (the DAS Explorer's per-column filters are f_<field>).
PAGES = {
    "confluence": {
        "label": "Confluence",
        "path": "/v1/national/confluence",
        "keys": ("province", "visual", "measure", "grain", "groups", "level", "basis", "expr", "period"),
        "prefixes": (),
    },
    "das-explorer": {
        "label": "DAS Explorer",
        "path": "/v1/national/das-explorer",
        "keys": ("ds", "sort", "rows", "cols", "measure", "chart", "period"),
        "prefixes": ("f_",),
    },
}

MAX_VALUE = 400     # one setting's value (a filter expression is capped at 300 by its own parser)
MAX_QUERY = 2000    # the whole stored query string


def clean_view_query(page, raw):
    """Reduce `raw` -- a query string ("a=b&c=d", with or without the leading "?") or a link to
    the page (absolute, scheme-less or site-relative) -- to `page`'s recognised settings, as a
    canonical query string. Order and repeated keys are kept (a multi-value filter repeats its
    key). Raises ValueError with a message fit to show the user when the page is unknown, the
    link is for a different page, nothing recognised is left, or it is too long.

    Only ever stored and handed back to the page's own script as settings: nothing here is used
    as a redirect target, so the link's host is irrelevant and is discarded with everything else
    that is not a recognised setting."""
    spec = PAGES.get(page)
    if spec is None:
        raise ValueError("That page doesn't have a starting view.")
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("There is nothing to save yet.")

    head, question, tail = raw.partition("?")
    if question and head:
        # A link: whatever came before the "?" must be this page.
        if "://" in head:
            path = urlsplit(head).path
        elif head.startswith("/"):
            path = head
        else:
            path = urlsplit("//" + head).path     # "host/path" with no scheme
        if path.rstrip("/") != spec["path"]:
            raise ValueError(f"That link isn't for the {spec['label']} page.")
        query = tail
    elif question:
        query = tail
    elif "=" in raw and not set(raw.split("=", 1)[0]) & set("/:"):
        # No "?": a bare query string starts with a setting name (a link would start with a
        # scheme, host or path). Values may well contain slashes or colons.
        query = raw
    else:
        raise ValueError(f"That link doesn't carry any {spec['label']} settings.")

    kept = []
    for key, value in parse_qsl(query, keep_blank_values=False):
        if key not in spec["keys"] and not any(key.startswith(p) and len(key) > len(p)
                                               for p in spec["prefixes"]):
            continue
        if len(key) > 80 or len(value) > MAX_VALUE:
            raise ValueError("That link is too long to save.")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in key + value):
            raise ValueError("That link contains characters that can't be saved.")
        kept.append((key, value))
    if not kept:
        raise ValueError(f"That link doesn't carry any {spec['label']} settings.")
    cleaned = urlencode(kept)
    if len(cleaned) > MAX_QUERY:
        raise ValueError("That link is too long to save.")
    return cleaned
