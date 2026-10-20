import { redirect } from "next/navigation";
import { Avatar, CheckIcon, ChevronIcon, Flash, flashFrom, Logo, type SearchParams } from "@/components/shell";
import { currentStaff } from "@/lib/auth";
import { grouped } from "@/lib/present";
import { getRun, latestRunId, runFigures, syntheticStaff } from "@/lib/queries";

export const dynamic = "force-dynamic";

const ROLE_TEXT: Record<string, string> = {
  analyst: "Works the exceptions queue",
  controller: "Works the queue and can requeue failed jobs",
};

/**
 * "Sign in as" picker over the synthetic staff (D-031). The accounts are printed openly: they
 * are deliberately public demo identities with no password, not secrets. Known fragility F6.
 * The summary on the left shows counts of the demo month's latest run, which are public
 * synthetic figures (D-099); no row, amount, explanation or hash is shown here.
 */
export default async function LoginPage({ searchParams }: { searchParams: SearchParams }) {
  if (await currentStaff()) redirect("/runs");
  const [staff, flash, latest] = await Promise.all([syntheticStaff(), flashFrom(searchParams), latestRunId()]);
  const [figures, run] = latest ? await Promise.all([runFigures(latest), getRun(latest)]) : [null, null];

  return (
    <main className="signin">
      <section className="hero">
        <span className="brand"><Logo size={28} /> Reconcile</span>
        <h1>Three sources.<br />One reconciled month.</h1>
        <p className="lead">
          Matches the internal ledger, the processor&apos;s settlement report and the bank statement —
          and puts only the genuine differences in front of a person. Any past run can be recomputed
          byte for byte.
        </p>
        {figures && run ? (
          <div className="sources">
            <div className="source"><strong>Internal ledger</strong><span className="mono">{grouped(figures.ledger_rows)} rows</span></div>
            <div className="source">
              <strong>Orrery settlement report</strong>
              <span className="mono">{grouped(figures.settlement_rows)} lines · {figures.settlement_payouts} payouts</span>
            </div>
            <div className="source"><strong>Demo Bank · camt.053</strong><span className="mono">{grouped(figures.bank_entries)} entries</span></div>
            <div className="summary">
              <CheckIcon />
              <strong>{run.matches} matches · {run.exceptions} exceptions</strong>
              <span className="muted">
                {figures.period_label}
                {figures.latest_replay_outcome ? ` · replay ${figures.latest_replay_outcome}` : ""}
              </span>
            </div>
          </div>
        ) : null}
      </section>

      <section className="card card-pad">
        <h2 style={{ fontSize: "1.375rem" }}>Sign in as a demo staff member</h2>
        <p className="muted" style={{ marginTop: "var(--space-2)" }}>
          These accounts are synthetic and public — there is no password. Choosing one simply tells
          the demo who is acting.
        </p>
        <Flash {...flash} />
        <div className="accounts">
          {staff.map((s) => (
            <form key={s.id} action="/api/session" method="post">
              <input type="hidden" name="staff_id" value={s.id} />
              <button type="submit" className="account" aria-label={`Sign in as ${s.display_name}`}>
                <Avatar name={s.display_name} role={s.role} large />
                <span className="grow">
                  <span className="name">{s.display_name}</span><br />
                  <span className="desc">{ROLE_TEXT[s.role] ?? s.role}</span>
                </span>
                <span className={`chip status ${s.role === "controller" ? "dark" : "neutral"}`}>{s.role}</span>
                <span className="chev"><ChevronIcon /></span>
              </button>
            </form>
          ))}
        </div>
        <p className="signin-foot">
          The system actor “Deployment seed” created the demo data and cannot sign in. Access is
          checked on the server for every page and action.
        </p>
      </section>
    </main>
  );
}
