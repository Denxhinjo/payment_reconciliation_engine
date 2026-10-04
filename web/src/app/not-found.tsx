import Link from "next/link";

export default function NotFound() {
  return (
    <main className="page narrow">
      <h1>Not found</h1>
      <p className="sub">There is nothing here, or it does not exist.</p>
      <Link href="/runs">Back to runs</Link>
    </main>
  );
}
