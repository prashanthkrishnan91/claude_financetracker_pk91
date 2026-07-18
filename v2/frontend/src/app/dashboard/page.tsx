import { redirect } from "next/navigation";

/** The dashboard root has no view of its own — Positions is the home view. */
export default function DashboardPage() {
  redirect("/dashboard/portfolio");
}
