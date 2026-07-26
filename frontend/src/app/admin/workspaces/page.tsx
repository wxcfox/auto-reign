import { redirect } from "next/navigation";

/**
 * Workspaces are managed from one list for every role: a public Workspace is a
 * template whose files always belong to the caller, so it needs no admin page.
 */
export default function GlobalWorkspacesPage() {
  return redirect("/workspaces");
}
