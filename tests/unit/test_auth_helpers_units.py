"""validate_password rule matrix. Note the 72-byte cap is bytes, not characters -
bcrypt silently truncates at 72 bytes, so multi-byte passphrases hit it early."""
import pytest

from data_viz.auth.auth_helpers import validate_password


def test_valid_password():
    ok, message = validate_password("Sufficiently-strong-pw1!")
    assert ok is True


@pytest.mark.parametrize("password,fragment", [
    ("", "required"),
    (None, "required"),
    ("Short-pw1!", "at least 12 characters"),
    ("a" * 70 + "A1!", "at most 72 bytes"),
    ("all-lowercase-pw1!", "uppercase"),
    ("ALL-UPPERCASE-PW1!", "lowercase"),
    ("No-digits-here!", "number"),
    ("NoSpecials123456", "special character"),
])
def test_rejections(password, fragment):
    ok, message = validate_password(password)
    assert ok is False
    assert fragment in message


def test_exactly_12_chars_passes():
    assert validate_password("Abcdefghij1!")[0] is True


def test_byte_cap_not_char_cap():
    # 24 chars x 3 bytes (euro sign) = 72 bytes -> passes the byte check; add "A1!a"
    # to satisfy char classes while staying within budget? No - build explicitly:
    # 68 bytes of multibyte + 4 ascii = 72 bytes, 26 chars -> passes.
    within = "€" * 22 + "Aa1!"      # 22*3 + 4 = 70 bytes
    over = "€" * 23 + "Aa1!"        # 23*3 + 4 = 73 bytes
    assert validate_password(within)[0] is True
    assert validate_password(over) == (False, "Password must be at most 72 bytes long.")
