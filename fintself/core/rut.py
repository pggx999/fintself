import re
from typing import Optional


def _compute_dv(body: str) -> str:
    """Computes the Chilean RUT check digit (modulo 11)."""
    total = 0
    factor = 2
    for digit in reversed(body):
        total += int(digit) * factor
        factor = 2 if factor == 7 else factor + 1
    remainder = 11 - (total % 11)
    if remainder == 11:
        return "0"
    if remainder == 10:
        return "K"
    return str(remainder)


def normalize_rut(rut: Optional[str]) -> Optional[str]:
    """Normalizes a RUT to 'BODY-DV' without dots (e.g. '12.345.678-5' -> '12345678-5').

    Returns None if the string does not look like a RUT. It does not check the
    verification digit; use `is_valid_rut` for that.
    """
    if not rut:
        return None
    cleaned = re.sub(r"[^0-9kK]", "", rut).upper()
    if len(cleaned) < 2 or not cleaned[:-1].isdigit():
        return None
    body = cleaned[:-1].lstrip("0")
    if not body:
        return None
    return f"{body}-{cleaned[-1]}"


def is_valid_rut(rut: Optional[str]) -> bool:
    """Returns True if the RUT is well formed and its check digit is correct."""
    normalized = normalize_rut(rut)
    if not normalized:
        return False
    body, dv = normalized.split("-")
    return _compute_dv(body) == dv
