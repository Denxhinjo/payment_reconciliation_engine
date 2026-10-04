import { redirect } from "next/navigation";
import { currentStaff } from "@/lib/auth";

export const dynamic = "force-dynamic";

export default async function Home() {
  redirect((await currentStaff()) ? "/runs" : "/login");
}
