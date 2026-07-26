import { redirect } from "next/navigation";

/** Public knowledge is reachable from the Public tab on the main page. */
export default function GlobalKnowledgePage() {
  return redirect("/knowledge");
}
