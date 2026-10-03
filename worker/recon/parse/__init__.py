"""Parsers: raw bytes in, typed records out. Pure functions with no database and no clock.

The importer stores their output in Postgres; the engine (stage 4) calls the same functions
on the stored raw bytes (D-007), so a parsed row and the row the engine sees can never
disagree. Any change to parsing behaviour must bump PARSER_VERSION.

Every parser fails closed: anything not understood rejects the whole file with a message that
names the line or element. Nothing is skipped, guessed or defaulted.
"""

from __future__ import annotations

import re
from datetime import date

PARSER_VERSION = "1.0.0"

# Level 1 reconciles a single currency (design §3). Its ISO 4217 minor-unit exponent is 2
# (source S6, SIX "List One", CcyMnrUnts for EUR).
LEVEL1_CURRENCY = "EUR"
LEVEL1_EXPONENT = 2

_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_INTEGER = re.compile(r"^-?[0-9]+$")


class ParseError(ValueError):
    """The file is rejected. The message says where and why."""


def strict_date(text: str, where: str) -> date:
    """Exactly YYYY-MM-DD. (date.fromisoformat alone also accepts e.g. '20260901'.)"""
    if not _ISO_DATE.match(text):
        raise ParseError(f"{where}: {text!r} is not a date in YYYY-MM-DD form")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ParseError(f"{where}: {text!r} is not a valid date") from exc


def strict_int(text: str, where: str) -> int:
    """An integer count of minor units: optional minus sign and digits only."""
    if not _INTEGER.match(text):
        raise ParseError(f"{where}: {text!r} is not an integer amount in minor units")
    return int(text)
