"""Job claiming, leases and the lease sweep (D-081). One implementation for every job kind.

claim()   marks one queued job 'running', increments attempts (so a crashed attempt still
          counts), and takes a lease (lease_until = now() + recon_job_lease()). Jobs at the attempt
          budget are never claimed (poison-pill protection, D-074).
finish()  records the outcome, but only while this worker still holds the lease. If the lease
          lapsed and the job was swept meanwhile, the update matches nothing and LeaseLost is
          raised, so the caller's transaction (e.g. a parse's rows) rolls back rather than
          committing work for a job that is no longer this worker's.
expire_leases()
          the sweep, run at the start of every worker invocation: jobs still 'running' past
          their lease become 'failed' ("lease expired: worker did not finish"), attempts kept.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg

LEASE_EXPIRED_ERROR = "lease expired: worker did not finish"


class LeaseLost(Exception):
    """This worker's lease on the job lapsed before it finished; its result was not recorded."""


@dataclass(frozen=True)
class Claim:
    job_id: int
    lease_until: datetime
    arguments: tuple


def claim(conn: psycopg.Connection, kind: str, columns: str, job_id: int | None = None) -> Claim | None:
    """Claim the given queued job of ``kind`` (or the oldest). ``columns`` are returned as arguments."""
    with conn.transaction():
        row = conn.execute(
            f"SELECT id, {columns} FROM job "
            "WHERE kind = %s AND status = 'queued' AND attempts < recon_max_job_attempts() "
            "AND (%s::bigint IS NULL OR id = %s) "
            "ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED",
            (kind, job_id, job_id),
        ).fetchone()
        if row is None:
            return None
        (lease_until,) = conn.execute(
            "UPDATE job SET status = 'running', attempts = attempts + 1, started_at = now(), "
            "lease_until = now() + recon_job_lease() WHERE id = %s RETURNING lease_until",
            (row[0],),
        ).fetchone()
        return Claim(row[0], lease_until, tuple(row[1:]))


def finish(conn: psycopg.Connection, claimed: Claim, status: str, *, error: str | None = None,
           run_id: int | None = None) -> None:
    """Record 'done' or 'failed' for a job this worker holds. Raises LeaseLost otherwise."""
    if status not in ("done", "failed"):
        raise ValueError(status)
    updated = conn.execute(
        "UPDATE job SET status = %s, finished_at = now(), error = %s, run_id = coalesce(%s, run_id) "
        "WHERE id = %s AND status = 'running' AND lease_until = %s",
        (status, error, run_id, claimed.job_id, claimed.lease_until),
    ).rowcount
    if updated != 1:
        raise LeaseLost(
            f"job {claimed.job_id}: the lease taken at claim (until {claimed.lease_until.isoformat()}) "
            "is no longer held; the outcome was not recorded"
        )


def fail_quietly(conn: psycopg.Connection, claimed: Claim, error: str) -> None:
    """Mark a held job failed after an unexpected error. If the lease is already gone (the sweep
    got there first), the job is failed already; nothing more to do."""
    try:
        with conn.transaction():
            finish(conn, claimed, "failed", error=error)
    except LeaseLost:
        pass


def expire_leases(conn: psycopg.Connection) -> list[int]:
    """The sweep. Returns the ids of the jobs it marked failed."""
    with conn.transaction():
        return [job_id for (job_id,) in conn.execute("SELECT expire_job_leases()")]
