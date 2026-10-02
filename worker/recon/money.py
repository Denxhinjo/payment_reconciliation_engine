"""Money helpers. Amounts are integer minor units everywhere (D-001); no float ever appears."""

from __future__ import annotations


def format_minor(amount_minor: int, exponent: int = 2) -> str:
    """Render minor units as a plain decimal string: 123450 -> '1234.50', -5 -> '-0.05'.

    Pure integer arithmetic. ``exponent`` is the ISO 4217 minor-unit count (EUR: 2, source S6).
    """
    if not isinstance(amount_minor, int) or isinstance(amount_minor, bool):
        raise TypeError(f"amount must be an int of minor units, got {type(amount_minor).__name__}")
    sign = "-" if amount_minor < 0 else ""
    whole, fraction = divmod(abs(amount_minor), 10**exponent)
    if exponent == 0:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{fraction:0{exponent}d}"
