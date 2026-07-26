import { redirect } from "next/navigation";

/** Public agents are reachable from the Public tab on the main page. */
export default function GlobalAgentsPage() {
  return redirect("/agents");
}
