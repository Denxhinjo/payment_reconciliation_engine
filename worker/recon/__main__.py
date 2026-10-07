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

    worker_cmd = commands.add_parser(
        "worker", help="process all queued parse and reconcile jobs, then exit")
    _database_argument(worker_cmd)

    reconcile_cmd = commands.add_parser(
        "reconcile", help="run the engine on three imported files and record the run")
    _database_argument(reconcile_cmd)
    reconcile_cmd.add_argument("--ledger-file", type=int, required=True, help="import_file id")
    reconcile_cmd.add_argument("--settlement-file", type=int, required=True, help="import_file id")
    reconcile_cmd.add_argument("--bank-file", type=int, required=True, help="import_file id")

    replay_cmd = commands.add_parser(
        "replay", help="recompute a finished run from its stored files and compare result hashes")
    _database_argument(replay_cmd)
    replay_cmd.add_argument("--run", type=int, required=True)

    queue_cmd = commands.add_parser("queue", help="list a run's exceptions")
    _database_argument(queue_cmd)
    queue_cmd.add_argument("--run", type=int, required=True)
    queue_cmd.add_argument("--status", choices=["all", "open", "resolved"], default="all")

    show_cmd = commands.add_parser("show", help="show one exception: evidence and resolution history")
    _database_argument(show_cmd)
    show_cmd.add_argument("--exception", type=int, required=True)

    reasons_cmd = commands.add_parser("reasons", help="list the resolution reason codes")
    _database_argument(reasons_cmd)

    for name, help_text in (("resolve", "record the first resolution of an exception"),
                            ("correct", "supersede the resolution in force with a new one")):
        cmd = commands.add_parser(name, help=help_text)
        _database_argument(cmd)
        cmd.add_argument("--exception", type=int, required=True)
        if name == "correct":
            cmd.add_argument("--supersedes", type=int, required=True,
                             help="id of the resolution in force being corrected")
        cmd.add_argument("--reason", required=True, help="a code from `recon reasons`")
        cmd.add_argument("--note", required=True, help="mandatory written note (at least 10 characters)")
        cmd.add_argument("--staff", default="Demo Analyst 1", help="display name of the resolver")

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

    from recon import runs

    # Provenance only, never part of the result: the commit the engine ran from, when known.
    git_sha = os.environ.get("GITHUB_SHA")

    if args.command == "worker":
        from recon import jobs

        with _connect(parser, args.database_url) as conn:
            # First, every invocation: fail jobs whose worker died (lease lapsed), attempts kept.
            expired = jobs.expire_leases(conn)
            for job_id in expired:
                print(f"job #{job_id}: {jobs.LEASE_EXPIRED_ERROR}; marked failed, attempts kept")
            outcomes = importer.run_parse_jobs(conn)
            reconciled = []
            while (done := runs.process_reconcile_job(conn, engine_git_sha=git_sha)) is not None:
                reconciled.append(done)
            replayed = []
            while (done := runs.process_replay_job(conn, engine_git_sha=git_sha)) is not None:
                replayed.append(done)
        for o in outcomes:
            print(f"job #{o.job_id}: file #{o.file_id} {o.status}"
                  + (f" ({o.rows} rows)" if o.status == "parsed" else f": {o.error}"))
        for job_id, run in reconciled:
            print(f"job #{job_id}: run #{run.run_id} {run.status}"
                  + (f": {run.error}" if run.error else ""))
        for job_id, replay in replayed:
            if isinstance(replay, str):
                print(f"job #{job_id}: replay refused: {replay}")
            else:
                print(f"job #{job_id}: run #{replay.replay_run_id} replays run #{replay.original_run_id}: "
                      + ("IDENTICAL" if replay.identical else "DIFFERENT"))
        print(f"{len(outcomes)} parse job(s), {len(reconciled)} reconcile job(s) and "
              f"{len(replayed)} replay job(s) processed")
        return 0

    if args.command == "replay":
        with _connect(parser, args.database_url) as conn:
            try:
                replay = runs.replay(conn, args.run, engine_git_sha=git_sha)
            except runs.ReplayRefused as exc:
                print(f"replay refused: {exc}", file=sys.stderr)
                return 1
        print(f"run #{replay.replay_run_id} replays run #{replay.original_run_id}")
        print(f"  original result sha256 {replay.original_sha256}")
        print(f"  replay   result sha256 {replay.replay_sha256 or '(none: ' + str(replay.error) + ')'}")
        if replay.identical:
            print("  IDENTICAL: the run was reproduced byte for byte")
            return 0
        print("  DIFFERENT: the replay did not reproduce the original result", file=sys.stderr)
        return 1

    if args.command == "reconcile":
        with _connect(parser, args.database_url) as conn:
            run = runs.create_and_run(conn, args.ledger_file, args.settlement_file, args.bank_file,
                                      engine_git_sha=git_sha)
        if run.status != "finished":
            print(f"run #{run.run_id} FAILED: {run.error}", file=sys.stderr)
            return 1
        print(f"run #{run.run_id} finished: {run.matches} matches, {run.exceptions} exceptions, "
              f"result sha256 {run.result_sha256}")
        return 0

    from recon import resolutions
    from recon.money import format_minor

    if args.command == "reasons":
        with _connect(parser, args.database_url) as conn:
            for code, label in resolutions.reason_codes(conn):
                print(f"{code:34} {label}")
        return 0

    if args.command == "queue":
        with _connect(parser, args.database_url) as conn:
            items = resolutions.queue(conn, args.run, args.status)
        for item in items:
            state = (f"resolved: {item.current_reason_code} by {item.resolved_by}"
                     if item.status == "resolved" else "OPEN")
            print(f"#{item.exception_id:<6} {item.suggested_reason:20} {state}")
            print(f"        {item.explanation}")
        print(f"{len(items)} exception(s), {sum(i.status == 'open' for i in items)} open")
        return 0

    if args.command == "show":
        with _connect(parser, args.database_url) as conn:
            try:
                shown = resolutions.detail(conn, args.exception)
            except LookupError as exc:
                print(str(exc), file=sys.stderr)
                return 1
        item = shown.item
        print(f"exception #{item.exception_id} (run #{item.run_id}, #{item.ordinal}): "
              f"suggested {item.suggested_reason}; {item.status.upper()}")
        print(f"  {item.explanation}")
        print("  evidence:")
        for row in shown.evidence:
            print(f"    {row.source:10} {row.identifier:28} {row.on.isoformat()} "
                  f"{row.reference or '':24} EUR {format_minor(row.amount_minor):>12}  {row.detail}")
        print("  resolution history (oldest first):")
        for record in shown.history or ():
            marker = "IN FORCE" if record.in_force else "superseded"
            print(f"    #{record.resolution_id} {marker}: {record.reason_code} by {record.resolved_by} "
                  f"at {record.created_at.isoformat(timespec='seconds')}")
            print(f"        {record.note}")
        if not shown.history:
            print("    (none)")
        return 0

    if args.command in ("resolve", "correct"):
        with _connect(parser, args.database_url) as conn:
            staff_id = importer.staff_id_by_name(conn, args.staff)
            try:
                if args.command == "resolve":
                    resolution_id = resolutions.resolve(
                        conn, args.exception, args.reason, args.note, staff_id)
                else:
                    resolution_id = resolutions.correct(
                        conn, args.exception, args.supersedes, args.reason, args.note, staff_id)
            except resolutions.ResolutionRefused as exc:
                print(f"refused: {exc}", file=sys.stderr)
                return 1
        print(f"resolution #{resolution_id} recorded for exception #{args.exception}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
