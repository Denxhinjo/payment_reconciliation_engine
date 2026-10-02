"""ISO 20022 camt.053.001.02 support: namespace, official schema, safe validation.

The schema is the official ISO 20022 XSD committed unmodified at
docs/sources/iso20022/camt.053.001.02.xsd (source S1 in docs/design.md, hash-confirmed by
the project owner against the ISO 20022 message archive). Element names used anywhere in
this package come from that file, never from memory.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from lxml import etree

NAMESPACE = "urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "sources" / "iso20022" / "camt.053.001.02.xsd"
)
SCHEMA_SHA256 = "d664afd198d1f36386a14e2bfd0505c80c1291a5357d8132aa6ab5443a6f2f3d"


class Camt053Error(ValueError):
    """The document is not a valid camt.053.001.02 message."""


def safe_parser() -> etree.XMLParser:
    """Parser for untrusted bank files: no external entities, no DTD loading, no network."""
    return etree.XMLParser(
        resolve_entities=False,
        load_dtd=False,
        no_network=True,
        huge_tree=False,
        remove_blank_text=False,
    )


@lru_cache(maxsize=1)
def schema() -> etree.XMLSchema:
    raw = SCHEMA_PATH.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != SCHEMA_SHA256:
        # Fail closed: never validate against a schema that is not the cited official file.
        raise Camt053Error(
            f"camt.053.001.02 schema at {SCHEMA_PATH} has sha256 {actual}, expected {SCHEMA_SHA256}"
        )
    return etree.XMLSchema(etree.fromstring(raw, safe_parser()))


def validate(document: bytes) -> etree._Element:
    """Parse ``document`` safely and validate it against the official XSD.

    Returns the root element. Raises Camt053Error with the validator's messages otherwise.
    """
    try:
        root = etree.fromstring(document, safe_parser())
    except etree.XMLSyntaxError as exc:
        raise Camt053Error(f"not well-formed XML: {exc}") from exc
    # A bank statement has no use for a DTD; one can only carry entity tricks. Refuse it.
    if root.getroottree().docinfo.doctype:
        raise Camt053Error("document declares a DTD (<!DOCTYPE ...>); refused")
    if root.tag != f"{{{NAMESPACE}}}Document":
        raise Camt053Error(f"root element is {root.tag!r}, expected Document in {NAMESPACE}")
    xsd = schema()
    try:
        valid = xsd.validate(root)
    except etree.XMLSchemaValidateError as exc:
        raise Camt053Error(f"XSD validation could not complete: {exc}") from exc
    if not valid:
        messages = "; ".join(f"line {e.line}: {e.message}" for e in xsd.error_log)
        raise Camt053Error(f"fails camt.053.001.02 XSD validation: {messages}")
    return root
