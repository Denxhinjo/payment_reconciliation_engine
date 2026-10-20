import Link from "next/link";
import type { Staff } from "@/lib/auth";
import { flashMessage, OVERDUE_NOTE, QUEUED_NOTE, RUNNING_NOTE } from "@/lib/messages";
import { openBadge } from "@/lib/queries";

export type NavKey = "runs" | "exceptions" | "upload" | "jobs";

/** The brand mark: three overlapping circles, the three sources. */
export function Logo({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" fill="none" strokeWidth="1.8">
      <circle cx="8.5" cy="9" r="5.5" stroke="currentColor" />
      <circle cx="15.5" cy="9" r="5.5" stroke="var(--accent)" />
      <circle cx="12" cy="15" r="5.5" stroke="currentColor" />
    </svg>
  );
}

/** "Demo Analyst 1" -> "A1", "Demo Controller" -> "C". Presentation only. */
export function initials(displayName: string): string {
  const words = displayName.replace(/^Demo\s+/i, "").split(/\s+/).filter(Boolean);
  return words.map((w) => (/^\d+$/.test(w) ? w : w.charAt(0))).join("").toUpperCase().slice(0, 2);
}

export function Avatar({ name, role, large }: { name: string; role?: string; large?: boolean }) {
  return (
    <span className={`avatar${large ? " lg" : ""}${role === "controller" ? " controller" : ""}`} aria-hidden="true">
      {initials(name)}
    </span>
  );
}

/** The signed-in shell: navigation, who you are, sign out. Presentation only; every page and
 * every POST route enforces access itself (lib/auth.ts). The badge is the open-exception count
 * of the run the dashboard opens on, read from the database (D-076). */
export async function Shell({ staff, active, children }: { staff: Staff; active?: NavKey; children: React.ReactNode }) {
  const badge = await openBadge();
  const item = (key: NavKey, href: string, label: string, extra?: React.ReactNode) => (
    <Link href={href} aria-current={active === key ? "page" : undefined}>{label}{extra}</Link>
  );
  return (
    <div className="desk">
      <header className="topbar">
        <Link href="/runs" className="brand"><Logo /> Reconcile</Link>
        <nav className="nav" aria-label="Main">
          {item("runs", "/runs", "Runs")}
          {item("exceptions", "/exceptions", "Exceptions",
            badge && badge !== "0" ? <span className="badge" aria-label={`${badge} open`}>{badge}</span> : null)}
          {item("upload", "/upload", "Upload")}
          {item("jobs", "/jobs", "Jobs")}
        </nav>
        <span className="spacer" />
        <span className="who">
          <Avatar name={staff.displayName} role={staff.role} />
          <span>
            <span className="who-name">{staff.displayName}</span><br />
            <span className="who-role">{staff.role}</span>
          </span>
        </span>
        <form action="/api/session/end" method="post">
          <button type="submit" className="button small">Sign out</button>
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

const STATUS_TONE: Record<string, string> = {
  finished: "ok", identical: "ok", done: "ok", resolved: "ok", parsed: "ok",
  failed: "bad", different: "bad", rejected: "bad",
  running: "neutral", queued: "neutral", open: "outline",
};

/** A status as recorded; capitalised by CSS only, so the text stays the stored value. */
export function StatusChip({ status }: { status: string | null }) {
  if (!status) return <span className="dim">—</span>;
  return <span className={`chip status ${STATUS_TONE[status] ?? "neutral"}`}>{status}</span>;
}

/** A suggested reason or match pass, as the engine names it. */
export function ReasonChip({ reason, label }: { reason: string; label?: string }) {
  const tone = reason === "timing" ? "slate" : reason === "exact" || reason === "gross_net" || reason === "many_to_one" ? "ok" : "warn";
  return <span className={`chip reason ${tone}`}>{label ?? reason}</span>;
}

/** Who did something. The system actor ("Deployment seed") is labelled wherever a name appears,
 * so seeded records are never mistaken for a person's work (D-091). */
export function Actor({ name, role }: { name: string | null; role: string | null }) {
  if (!name) return <span className="dim">—</span>;
  return role === "system"
    ? <>{name} <span className="chip system" title="System actor: not a person">System</span></>
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

// --- Icons (decorative; the text beside each says the same) --------------------------------------
type IconProps = { size?: number };
const svg = (size: number, children: React.ReactNode) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
);
export const CheckIcon = ({ size = 16 }: IconProps) => svg(size, <path d="M5 12.5l4.5 4.5L19 7.5" />);
export const ChevronIcon = ({ size = 18 }: IconProps) => svg(size, <path d="M9 6l6 6-6 6" />);
export const InfoIcon = ({ size = 16 }: IconProps) => svg(size, <><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>);
export const ShieldIcon = ({ size = 16 }: IconProps) => svg(size, <path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z" />);
export const LockIcon = ({ size = 14 }: IconProps) => svg(size, <><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 018 0v3" /></>);
export const UploadIcon = ({ size = 22 }: IconProps) => svg(size, <path d="M12 15V4M7.5 8.5L12 4l4.5 4.5M5 15v3a2 2 0 002 2h10a2 2 0 002-2v-3" />);
export const ReplayIcon = ({ size = 15 }: IconProps) => svg(size, <><path d="M4 4v6h6" /><path d="M20 12a8 8 0 01-14.7 4.4M4 10a8 8 0 0114.7-1.4" /></>);
export const PlusIcon = ({ size = 15 }: IconProps) => svg(size, <path d="M12 5v14M5 12h14" />);
export const CheckCircle = ({ size = 22 }: IconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="12" cy="12" r="11" fill="var(--ok-bg)" />
    <path d="M7 12.5l3.2 3.2L17 9" fill="none" stroke="var(--ok-text)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);
