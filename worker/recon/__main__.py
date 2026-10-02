"""Command-line entry point: ``python -m recon <command>``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from recon.migrate import DEFAULT_MIGRATIONS_DIR, MigrationError, migrate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recon")
    commands = parser.add_subparsers(dest="command", required=True)

    migrate_cmd = commands.add_parser("migrate", help="apply pending database migrations")
    migrate_cmd.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL connection string (default: $DATABASE_URL)",
    )
    migrate_cmd.add_argument("--dir", type=Path, default=DEFAULT_MIGRATIONS_DIR)

    generate_cmd = commands.add_parser(
        "generate", help="write a synthetic month (ledger, settlement report, camt.053, planted.json)"
    )
    generate_cmd.add_argument("--seed", type=int, required=True)
    generate_cmd.add_argument("--month", required=True, help="YYYY-MM")
    generate_cmd.add_argument("--out", type=Path, required=True)

    args = parser.parse_args(argv)

    if args.command == "generate":
        from recon.generate import generate

        for path in generate(args.seed, args.month).write(args.out):
            print(f"wrote {path}")
        return 0

    if args.command == "migrate":
        if not args.database_url:
            parser.error("no database URL: pass --database-url or set DATABASE_URL")
        try:
            applied = migrate(args.database_url, args.dir)
        except MigrationError as exc:
            print(f"migration refused: {exc}", file=sys.stderr)
            return 1
        print("applied: " + ", ".join(applied) if applied else "database is up to date")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
