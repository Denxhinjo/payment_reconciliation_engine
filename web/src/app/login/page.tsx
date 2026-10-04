import { redirect } from "next/navigation";
import { Flash, flashFrom, type SearchParams } from "@/components/shell";
import { currentStaff } from "@/lib/auth";
import { syntheticStaff } from "@/lib/queries";

export const dynamic = "force-dynamic";

/**
 * "Sign in as" picker over the synthetic staff (D-031). The accounts are printed openly: they
 * are deliberately public demo identities with no password, not secrets. Known fragility F6.
 */
export default async function LoginPage({ searchParams }: { searchParams: SearchParams }) {
  if (await currentStaff()) redirect("/runs");
  const staff = await syntheticStaff();
  const flash = await flashFrom(searchParams);

  return (
    <main className="page narrow">
      <h1>Sign in as a demo staff member</h1>
      <p className="sub">
        These accounts are synthetic and public. There is no password. Analysts work the
        exceptions queue; the controller can also requeue failed jobs. Choosing one simply
        tells the demo who is acting.
      </p>
      <Flash {...flash} />
      <table className="table">
        <thead>
          <tr><th>Account</th><th>Role</th><th /></tr>
        </thead>
        <tbody>
          {staff.map((s) => (
            <tr key={s.id}>
              <td>{s.display_name}</td>
              <td><span className="chip neutral">{s.role}</span></td>
              <td>
                <form action="/api/session" method="post">
                  <input type="hidden" name="staff_id" value={s.id} />
                  <button type="submit" className="button">Sign in as {s.display_name}</button>
                </form>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
