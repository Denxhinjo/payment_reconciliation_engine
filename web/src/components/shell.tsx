import Link from "next/link";
import type { Staff } from "@/lib/auth";
import { flashMessage, OVERDUE_NOTE, QUEUED_NOTE, RUNNING_NOTE } from "@/lib/messages";

/** The signed-in shell: navigation, who you are, sign out. Presentation only; every page and
 * every POST route enforces access itself (lib/auth.ts). */
export function Shell({ staff, children }: { staff: Staff; children: React.ReactNode }) {
  return (
    <div className="desk">
      <header className="desk-bar">
        <Link href="/runs" className="desk-brand">Reconciliation</Link>
        <nav className="desk-nav">
          <Link href="/runs">Runs</Link>
          <Link href="/upload">Upload</Link>
          <Link href="/jobs">Jobs</Link>
        </nav>
        <span className="desk-spacer" />
        <span className="desk-user">
          {staff.displayName} <span className="chip neutral">{staff.role}</span>
        </span>
        <form action="/api/session/end" method="post">
          <button type="submit" className="linkish">Sign out</button>
        </form>
      </header>
      {children}
    </div>
  );
}

/** Outcome of the previous action: fixed text looked up from the code in the URL (D-082).
 * Callers pass the already-resolved messages from flashFrom(); nothing from the URL is shown. */
export function Flash({ notice, error }: { notice?: string | null; error?: string | null }) {
  return (
    <>
      {notice ? <p className="flash ok" role="status">{notice}</p> : null}
      {error ? <p className="flash bad" role="alert">{error}</p> : null}
    </>
  );
}

export function StatusChip({ status }: { status: string | null }) {
  const tone: Record<string, string> = {
    finished: "ok", identical: "ok", done: "ok", resolved: "ok", parsed: "ok",
    failed: "bad", different: "bad", rejected: "bad",
    running: "neutral", queued: "neutral", open: "warn",
  };
  if (!status) return <span className="dim">—</span>;
  return <span className={`chip ${tone[status] ?? "neutral"}`}>{status}</span>;
}

/** Who did something. The system actor ("Deployment seed") is labelled as such everywhere a name
 * appears, so seeded records are never mistaken for a person's work (D-091). */
export function Actor({ name, role }: { name: string | null; role: string | null }) {
  if (!name) return <span className="dim">—</span>;
  return role === "system"
    ? <>{name} <span className="chip neutral">system actor</span></>
    : <>{name}</>;
}

export type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export async function flashFrom(searchParams: SearchParams) {
  const params = await searchParams;
  const pick = (key: string) => (typeof params[key] === "string" ? (params[key] as string) : undefined);
  return {
    notice: flashMessage("notice", pick("notice"), pick("ref")),
    error: flashMessage("error", pick("error"), pick("ref")),
  };
}

/** When a queued job will run, or that it is overdue: the database decides `overdue` (D-088). */
export function QueueStatus({ status, overdue }: { status: string; overdue: boolean }) {
  if (status === "running") return <span className="queue-note">{RUNNING_NOTE}</span>;
  if (status !== "queued") return null;
  return overdue
    ? <span className="queue-note bad-text" role="alert">{OVERDUE_NOTE}</span>
    : <span className="queue-note">{QUEUED_NOTE}</span>;
}
