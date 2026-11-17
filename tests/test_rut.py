import pytest

from fintself.core.rut import is_valid_rut, normalize_rut


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("12.345.678-5", "12345678-5"),
        ("12345678-5", "12345678-5"),
        ("123456785", "12345678-5"),
        ("7.654.321-k", "7654321-K"),
        ("", None),
        ("abc", None),
    ],
)
def test_normalize_rut(raw, expected):
    assert normalize_rut(raw) == expected


@pytest.mark.parametrize(
    "rut,valid",
    [
        ("12.345.678-5", True),
        ("11.111.111-1", True),
        ("76.086.428-5", True),
        ("12.345.678-0", False),
        ("1-9", True),
        ("", False),
    ],
)
def test_is_valid_rut(rut, valid):
    assert is_valid_rut(rut) is valid
