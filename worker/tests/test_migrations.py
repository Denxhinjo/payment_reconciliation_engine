"""The migration runner applies the real migrations and refuses untrustworthy histories."""

from __future__ import annotations

import shutil
from pathlib import Path

import psycopg
import pytest

from recon.migrate import DEFAULT_MIGRATIONS_DIR, MigrationError, discover, migrate


def _copy_migrations(tmp_path: Path) -> Path:
    target = tmp_path / "migrations"
    shutil.copytree(DEFAULT_MIGRATIONS_DIR, target)
    return target


def test_real_migrations_apply_in_order_then_are_a_no_op(empty_database_url):
    applied = migrate(empty_database_url)
    assert applied == [m.filename for m in discover(DEFAULT_MIGRATIONS_DIR)]
    assert migrate(empty_database_url) == []


def test_applied_migration_edited_afterwards_is_refused(empty_database_url, tmp_path):
    directory = _copy_migrations(tmp_path)
    migrate(empty_database_url, directory)
    first = sorted(directory.iterdir())[0]
    first.write_bytes(first.read_bytes() + b"\n-- edited after being applied\n")
    with pytest.raises(MigrationError, match="edited after it was applied"):
        migrate(empty_database_url, directory)


def test_applied_migration_removed_from_disk_is_refused(empty_database_url, tmp_path):
    directory = _copy_migrations(tmp_path)
    migrate(empty_database_url, directory)
    sorted(directory.iterdir())[-1].unlink()
    with pytest.raises(MigrationError, match="missing from"):
        migrate(empty_database_url, directory)


def test_gap_in_numbering_is_refused(tmp_path):
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "0001_a.sql").write_text("SELECT 1;")
    (directory / "0003_c.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="no gaps"):
        discover(directory)


def test_unexpected_file_in_directory_is_refused(tmp_path):
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "0001_a.sql").write_text("SELECT 1;")
    (directory / "notes.txt").write_text("not a migration")
    with pytest.raises(MigrationError, match="unexpected entry"):
        discover(directory)


def test_failing_migration_leaves_database_untouched(empty_database_url, tmp_path):
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "0001_ok.sql").write_text("CREATE TABLE synthetic_probe (id int);")
    (directory / "0002_broken.sql").write_text("THIS IS NOT SQL;")
    with pytest.raises(psycopg.errors.SyntaxError):
        migrate(empty_database_url, directory)
    with psycopg.connect(empty_database_url) as conn:
        # Everything, including the first (valid) migration and the runner's own ledger
        # table, was rolled back together.
        assert conn.execute("SELECT to_regclass('synthetic_probe')").fetchone() == (None,)
        assert conn.execute("SELECT to_regclass('schema_migration')").fetchone() == (None,)
