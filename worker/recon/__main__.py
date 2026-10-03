"""Command-line entry point: ``python -m recon <command>``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from recon.migrate import DEFAULT_MIGRATIONS_DIR, MigrationError, migrate


def _database_argument(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL connection string (default: $DATABASE_URL)",
    )


def _connect(parser: argparse.ArgumentParser, url: str | None):
    import psycopg

    if not url:
        parser.error("no database URL: pass --database-url or set DATABASE_URL")
    return psycopg.connect(url, autocommit=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recon")
    commands = parser.add_subparsers(dest="command", required=True)

    migrate_cmd = commands.add_parser("migrate", help="apply pending database migrations")
    _database_argument(migrate_cmd)
    migrate_cmd.add_argument("--dir", type=Path, default=DEFAULT_MIGRATIONS_DIR)

    generate_cmd = commands.add_parser(
        "generate", help="write a synthetic month (ledger, settlement report, camt.053, planted.json)"
    )
    generate_cmd.add_argument("--seed", type=int, required=True)
    generate_cmd.add_argument("--month", required=True, help="YYYY-MM")
    generate_cmd.add_argument("--out", type=Path, required=True)

    seed_cmd = commands.add_parser("seed-staff", help="create the synthetic demo staff users")
    _database_argument(seed_cmd)

    import_cmd = commands.add_parser(
        "import", help="store a file (idempotent by SHA-256) and parse it now"
    )
    _database_argument(import_cmd)
    import_cmd.add_argument("--kind", required=True, choices=["ledger", "settlement", "bank"])
    import_cmd.add_argument("--file", type=Path, required=True)
    import_cmd.add_argument("--staff", default="Demo Analyst 1", help="display name of the uploader")

    worker_cmd = commands.add_parser("worker", help="process all queued parse jobs, then exit")
    _database_argument(worker_cmd)

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

    from recon import importer

    if args.command == "seed-staff":
        with _connect(parser, args.database_url) as conn:
            importer.seed_synthetic_staff(conn)
        print("synthetic staff: " + ", ".join(importer.SYNTHETIC_STAFF))
        return 0

    if args.command == "import":
        raw = args.file.read_bytes()
        with _connect(parser, args.database_url) as conn:
            staff_id = importer.staff_id_by_name(conn, args.staff)
            stored = importer.store_file(conn, args.kind, args.file.name, raw, staff_id)
            if not stored.created:
                print(f"already imported as file #{stored.file_id} ({stored.kind}); nothing done")
                return 0
            outcome = importer.process_parse_job(conn, stored.job_id)
        if outcome.status == "rejected":
            print(f"file #{outcome.file_id} stored but REJECTED: {outcome.error}", file=sys.stderr)
            return 1
        print(f"file #{outcome.file_id} imported and parsed: {outcome.rows} rows")
        return 0

    if args.command == "worker":
        with _connect(parser, args.database_url) as conn:
            outcomes = importer.run_parse_jobs(conn)
        for o in outcomes:
            print(f"job #{o.job_id}: file #{o.file_id} {o.status}"
                  + (f" ({o.rows} rows)" if o.status == "parsed" else f": {o.error}"))
        print(f"{len(outcomes)} parse job(s) processed")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
