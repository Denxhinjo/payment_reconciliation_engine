"""Numbered raw-SQL migration runner.

Migrations live in ``db/migrations`` as ``NNNN_name.sql`` and are applied in number order.
The runner records the SHA-256 of every applied file and refuses to run if:

- a file in the directory does not follow the naming rule (fail closed: nothing unexpected
  is silently skipped),
- the numbers are not exactly 1..n with no gaps or repeats,
- an already-applied migration has been edited, renamed or removed.

All pending migrations are applied in one transaction under an advisory lock, so a failed
migration leaves the database exactly as it was and two runners cannot interleave.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

FILENAME_RE = re.compile(r"^(?P<number>[0-9]{4})_[a-z0-9_]+\.sql$")

# Arbitrary constant identifying this runner's advisory lock: ASCII "recon".
ADVISORY_LOCK_KEY = 0x7265636F6E

DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"

CREATE_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS schema_migration (
  filename   text PRIMARY KEY,
  sha256     bytea NOT NULL CHECK (octet_length(sha256) = 32),
  applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(Exception):
    """The migrations directory or the database's migration history is not trustworthy."""


@dataclass(frozen=True)
class Migration:
    number: int
    filename: str
    sql: str
    sha256: bytes


def discover(directory: Path) -> list[Migration]:
    """Read and validate every migration file in ``directory``, in number order."""
    if not directory.is_dir():
        raise MigrationError(f"migrations directory not found: {directory}")

    migrations: list[Migration] = []
    for path in sorted(directory.iterdir(), key=lambda p: p.name):
        match = FILENAME_RE.match(path.name)
        if match is None or not path.is_file():
            raise MigrationError(
                f"unexpected entry in migrations directory: {path.name!r} "
                "(expected NNNN_lowercase_name.sql)"
            )
        raw = path.read_bytes()
        try:
            sql = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MigrationError(f"{path.name} is not valid UTF-8: {exc}") from exc
        migrations.append(
            Migration(
                number=int(match.group("number")),
                filename=path.name,
                sql=sql,
                sha256=hashlib.sha256(raw).digest(),
            )
        )

    numbers = [m.number for m in migrations]
    expected = list(range(1, len(migrations) + 1))
    if numbers != expected:
        raise MigrationError(
            f"migration numbers must be exactly {expected[:1]}..{expected[-1:]} with no gaps "
            f"or repeats; found {numbers}"
        )
    return migrations


def migrate(conninfo: str, directory: Path = DEFAULT_MIGRATIONS_DIR) -> list[str]:
    """Apply all pending migrations. Returns the filenames applied, in order."""
    migrations = discover(directory)

    with psycopg.connect(conninfo) as conn:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))
            conn.execute(CREATE_LEDGER_SQL)
            applied = {
                filename: bytes(sha)
                for filename, sha in conn.execute(
                    "SELECT filename, sha256 FROM schema_migration"
                ).fetchall()
            }

            on_disk = {m.filename: m for m in migrations}
            for filename, sha in sorted(applied.items()):
                migration = on_disk.get(filename)
                if migration is None:
                    raise MigrationError(
                        f"{filename} is recorded as applied but is missing from {directory}"
                    )
                if migration.sha256 != sha:
                    raise MigrationError(
                        f"{filename} was edited after it was applied "
                        f"(recorded sha256 {sha.hex()}, file sha256 {migration.sha256.hex()}); "
                        "write a new migration instead"
                    )

            # Applied migrations must be exactly the first k files; anything else means the
            # history and the directory have diverged.
            prefix = [m.filename for m in migrations[: len(applied)]]
            if sorted(prefix) != sorted(applied):
                raise MigrationError(
                    f"applied migrations {sorted(applied)} are not the first {len(applied)} "
                    f"files on disk {prefix}"
                )

            applied_now: list[str] = []
            for migration in migrations[len(applied):]:
                conn.execute(migration.sql)
                conn.execute(
                    "INSERT INTO schema_migration (filename, sha256) VALUES (%s, %s)",
                    (migration.filename, migration.sha256),
                )
                applied_now.append(migration.filename)

    return applied_now
