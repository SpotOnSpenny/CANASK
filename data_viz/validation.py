# Shared server-side input validators for form-handling routes.
#
# The trust boundary is the server: client-side maxlength/type/pattern attributes are UX + defence
# in depth, but every form field is (re)validated here before it reaches the DB or an external
# service. All validators return a uniform (ok: bool, value_or_message) tuple, matching the existing
# validate_password in data_viz/auth/auth_helpers.py:
#   ok True  -> second item is the cleaned/normalized value ready to use
#   ok False -> second item is a user-facing error message
#
# Length caps sit under the db.String(255) columns in data_viz/database/models.py so an over-length
# submission is rejected with a clear message instead of raising a DB DataError (HTTP 500) on commit.

import re
import unicodedata

from email_validator import validate_email as _validate_email, EmailNotValidError

from data_viz.auth.role_hierarchy import ROLE_HIERARCHY

# --- Length / format limits -------------------------------------------------------------------
MAX_EMAIL = 254            # RFC 5321 practical maximum; fits String(255)
USERNAME_PATTERN = r"[A-Za-z0-9._-]{3,30}"   # strict: letters, digits, and . _ - only
MAX_GROUP_NAME = 120
MAX_GROUP_DESC = 255
MAX_FEEDBACK_NAME = 100
MAX_FEEDBACK_BODY = 5000

_USERNAME_RE = re.compile(r"\A" + USERNAME_PATTERN + r"\Z")

# Invisible / bidirectional-control characters. These survive HTML autoescaping and enable
# display/homoglyph/bidi spoofing (e.g. a group name that visually reads differently than it stores),
# so free-text fields reject them outright.
_INVISIBLE_CHARS = {
    "​", "‌", "‍", "⁠", "﻿",           # zero-width space/joiners/BOM
    "‎", "‏",                                          # LTR/RTL marks
    "‪", "‫", "‬", "‭", "‮",           # bidi embeddings/overrides
    "⁦", "⁧", "⁨", "⁩",                     # bidi isolates
}


def _has_disallowed_chars(value, allow_newlines=False):
    """True if value contains a C0/C1 control character or an invisible/bidi-control character.
    When allow_newlines is set, tab/newline/carriage-return are permitted (multi-line fields)."""
    allowed = {"\t", "\n", "\r"} if allow_newlines else set()
    for ch in value:
        if ch in _INVISIBLE_CHARS:
            return True
        cp = ord(ch)
        if (cp < 0x20 or 0x7f <= cp <= 0x9f) and ch not in allowed:  # C0 controls, DEL, C1 controls
            return True
    return False


def validate_email(value, required=True):
    """Validate email syntax (no DNS/deliverability lookup) and normalize it. Returns
    (True, normalized) or (False, message). When not required, a blank value passes as (True, None)."""
    value = (value or "").strip()
    if not value:
        return (True, None) if not required else (False, "An email address is required.")
    if len(value) > MAX_EMAIL:
        return False, f"Email address must be at most {MAX_EMAIL} characters."
    try:
        info = _validate_email(value, check_deliverability=False)
    except EmailNotValidError:
        return False, "Please enter a valid email address."
    # email-validator v2 exposes .normalized; fall back to .email for older versions.
    return True, getattr(info, "normalized", None) or info.email


def validate_username(value):
    """Strict username policy: 3-30 chars of letters, digits, and . _ - only.
    Returns (True, value) or (False, message)."""
    value = (value or "").strip()
    if not value:
        return False, "A username is required."
    if not _USERNAME_RE.match(value):
        return False, ("Username must be 3-30 characters and use only letters, numbers, "
                       "and the symbols . _ -")
    return True, value


def validate_text(value, label, max_len, required=True, multiline=False):
    """Validate a free-text field. Returns (True, cleaned) or (False, message).

    The value is Unicode NFC-normalized (canonicalizes equivalent forms) and stripped; a blank
    optional value passes as (True, None). Values containing control, zero-width, or bidirectional
    characters are rejected (these survive HTML escaping and enable spoofing). multiline=True permits
    tab/newline/carriage-return (for genuinely multi-line fields)."""
    value = unicodedata.normalize("NFC", value or "").strip()
    if not value:
        return (True, None) if not required else (False, f"{label} is required.")
    if _has_disallowed_chars(value, allow_newlines=multiline):
        return False, f"{label} contains invalid or hidden characters. Please remove them."
    if len(value) > max_len:
        return False, f"{label} must be at most {max_len} characters."
    return True, value


def validate_role(role, allow_site_admin=False):
    """The submitted role string must be a known role. By default the group-scoped roles are allowed
    but "Site Admin" is not (site-admin is granted via the dedicated site_admin flag, not a group
    role). Returns (True, role) or (False, message)."""
    if role not in ROLE_HIERARCHY:
        return False, "Unknown role."
    if role == "Site Admin" and not allow_site_admin:
        return False, "Invalid role for a group assignment."
    return True, role
