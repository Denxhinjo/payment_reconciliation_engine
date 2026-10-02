"""import_file / import_parse: idempotent import, database-derived hash, raw bytes immutable."""

from __future__ import annotations

import hashlib

from tests.conftest import (
    APPEND_ONLY, CHECK_VIOLATION, FOREIGN_KEY_VIOLATION, GENERATED_ALWAYS, UNIQUE_VIOLATION,
    raises_sqlstate,
)


def test_sha256_is_computed_by_the_database_from_the_raw_bytes(conn, build):
    raw = b"SYNTHETIC DEMO DATA\nentry_id,amount_minor\n"
    file_id = build.file("ledger", raw=raw)
    (stored,) = conn.execute("SELECT sha256 FROM import_file WHERE id = %s", (file_id,)).fetchone()
    assert bytes(stored) == hashlib.sha256(raw).digest()


def test_sha256_cannot_be_supplied_by_the_caller(conn, build):
    staff = build.staff()
    with raises_sqlstate(conn, GENERATED_ALWAYS):
        conn.execute(
            "INSERT INTO import_file (kind, original_name, raw, sha256, uploaded_by) "
            "VALUES ('ledger', 'x.csv', 'abc', sha256('something else'), %s)",
            (staff,),
        )


def test_same_bytes_imported_twice_is_rejected(conn, build):
    raw = b"SYNTHETIC DEMO DATA identical file"
    build.file("ledger", raw=raw)
    with raises_sqlstate(conn, UNIQUE_VIOLATION):
        build.file("ledger", raw=raw)


def test_same_bytes_under_a_different_kind_is_still_the_same_file(conn, build):
    raw = b"SYNTHETIC DEMO DATA identical file, other slot"
    build.file("ledger", raw=raw)
    with raises_sqlstate(conn, UNIQUE_VIOLATION):
        build.file("bank", raw=raw)


def test_idempotent_insert_does_nothing_on_second_import(conn, build):
    """The insert the web layer uses: the second upload returns no new row."""
    staff = build.staff()
    query = (
        "INSERT INTO import_file (kind, original_name, raw, uploaded_by) "
        "VALUES ('settlement', 'orrery.csv', %s, %s) ON CONFLICT (sha256) DO NOTHING RETURNING id"
    )
    raw = b"SYNTHETIC DEMO DATA settlement"
    assert conn.execute(query, (raw, staff)).fetchone() is not None
    assert conn.execute(query, (raw, staff)).fetchone() is None
    assert conn.execute("SELECT count(*) FROM import_file").fetchone() == (1,)


def test_empty_file_is_rejected(conn, build):
    with raises_sqlstate(conn, CHECK_VIOLATION):
        build.file("ledger", raw=b"")


def test_raw_bytes_cannot_be_updated(conn, build):
    file_id = build.file("ledger")
    with raises_sqlstate(conn, APPEND_ONLY):
        conn.execute("UPDATE import_file SET raw = 'tampered' WHERE id = %s", (file_id,))


def test_raw_file_cannot_be_deleted(conn, build):
    file_id = build.file("ledger")
    with raises_sqlstate(conn, APPEND_ONLY):
        conn.execute("DELETE FROM import_file WHERE id = %s", (file_id,))


def test_parse_outcome_kind_must_match_file_kind(conn, build):
    file_id = build.file("ledger")
    with raises_sqlstate(conn, FOREIGN_KEY_VIOLATION):
        build.parse(file_id, "bank")


def test_rejected_parse_must_carry_an_error(conn, build):
    file_id = build.file("bank")
    with raises_sqlstate(conn, CHECK_VIOLATION):
        build.parse(file_id, "bank", status="rejected", error=None)


def test_successful_parse_cannot_carry_an_error(conn, build):
    file_id = build.file("bank")
    with raises_sqlstate(conn, CHECK_VIOLATION):
        build.parse(file_id, "bank", status="parsed", error="should not be here")


def test_a_file_has_exactly_one_parse_outcome(conn, build):
    file_id = build.file("bank")
    build.parse(file_id, "bank", status="rejected", error="XSD validation failed")
    with raises_sqlstate(conn, UNIQUE_VIOLATION):
        build.parse(file_id, "bank")


def test_parse_outcome_cannot_be_changed(conn, build):
    file_id = build.file("bank")
    build.parse(file_id, "bank", status="rejected", error="XSD validation failed")
    with raises_sqlstate(conn, APPEND_ONLY):
        conn.execute(
            "UPDATE import_parse SET status = 'parsed', error = NULL WHERE import_file_id = %s",
            (file_id,),
        )
