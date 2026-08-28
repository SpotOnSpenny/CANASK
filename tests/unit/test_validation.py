"""Validators share a uniform contract: (True, cleaned_value) or (False, user_message)."""
import pytest

from data_viz.validation import (
    MAX_EMAIL,
    MAX_GROUP_NAME,
    validate_email,
    validate_role,
    validate_text,
    validate_username,
)


class TestValidateEmail:
    def test_valid_email_normalized(self):
        ok, value = validate_email("  User@Example.COM ")
        assert ok is True
        assert value == "User@example.com"  # email-validator lowercases the domain only

    def test_invalid_syntax(self):
        ok, message = validate_email("not-an-email")
        assert ok is False
        assert "valid email" in message

    def test_blank_required(self):
        ok, message = validate_email("")
        assert ok is False
        assert "required" in message

    def test_blank_optional_passes_as_none(self):
        assert validate_email("", required=False) == (True, None)
        assert validate_email(None, required=False) == (True, None)

    def test_over_length_rejected(self):
        local = "a" * (MAX_EMAIL)
        ok, message = validate_email(local + "@example.com")
        assert ok is False
        assert str(MAX_EMAIL) in message

    def test_non_string_payload_rejected_not_crash(self):
        ok, _ = validate_email(["x@example.com"])
        assert ok is False


class TestValidateUsername:
    @pytest.mark.parametrize("name", ["abc", "user.name", "user_name-2", "a" * 30])
    def test_valid(self, name):
        assert validate_username(name) == (True, name)

    @pytest.mark.parametrize("name", [
        "ab",              # too short
        "a" * 31,          # too long
        "has space",
        "bad!char",
        "",
        None,
    ])
    def test_invalid(self, name):
        ok, message = validate_username(name)
        assert ok is False
        assert isinstance(message, str)

    def test_non_string_rejected(self):
        assert validate_username(123)[0] is False

    def test_strips_whitespace(self):
        assert validate_username("  abc  ") == (True, "abc")


class TestValidateText:
    def test_valid_stripped(self):
        assert validate_text("  hello  ", "Name", 100) == (True, "hello")

    def test_blank_required(self):
        ok, message = validate_text("", "Name", 100)
        assert (ok, message) == (False, "Name is required.")

    def test_blank_optional_is_none(self):
        assert validate_text("", "Name", 100, required=False) == (True, None)

    def test_over_length(self):
        ok, message = validate_text("a" * (MAX_GROUP_NAME + 1), "Name", MAX_GROUP_NAME)
        assert ok is False
        assert str(MAX_GROUP_NAME) in message

    def test_boundary_length_ok(self):
        value = "a" * MAX_GROUP_NAME
        assert validate_text(value, "Name", MAX_GROUP_NAME) == (True, value)

    def test_zero_width_characters_rejected(self):
        ok, message = validate_text("evil​name", "Name", 100)
        assert ok is False
        assert "hidden" in message

    def test_control_characters_rejected(self):
        assert validate_text("a\x00b", "Name", 100)[0] is False

    def test_newline_rejected_single_line(self):
        assert validate_text("line1\nline2", "Name", 100)[0] is False

    def test_newline_allowed_multiline(self):
        ok, value = validate_text("line1\nline2", "Body", 100, multiline=True)
        assert (ok, value) == (True, "line1\nline2")

    def test_bidi_override_rejected_even_multiline(self):
        assert validate_text("a‮b", "Body", 100, multiline=True)[0] is False

    def test_nfc_normalization(self):
        # e + combining acute normalizes to the precomposed character
        ok, value = validate_text("café", "Name", 100)
        assert (ok, value) == (True, "café")

    def test_non_string_rejected(self):
        assert validate_text({"x": 1}, "Name", 100)[0] is False


class TestValidateRole:
    @pytest.mark.parametrize("role", ["Data Owner", "Group Admin", "Data Viewer"])
    def test_group_assignable_roles(self, role):
        assert validate_role(role) == (True, role)

    def test_site_admin_never_a_group_role(self):
        ok, message = validate_role("Site Admin")
        assert ok is False
        assert "group assignment" in message

    def test_unknown_role(self):
        assert validate_role("Overlord") == (False, "Unknown role.")

    def test_case_sensitive(self):
        assert validate_role("data viewer")[0] is False
