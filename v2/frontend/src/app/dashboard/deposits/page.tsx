import { redirect } from "next/navigation";
import { LEGACY_ROUTE_REDIRECTS } from "@/lib/route-redirects";

/** Retired surface — permanently redirects to its canonical view. */
export default function Page() {
  redirect(LEGACY_ROUTE_REDIRECTS["/dashboard/deposits"]);
}
