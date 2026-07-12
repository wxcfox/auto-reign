import { RoleGuard } from "@/components/RoleGuard";
import { WorkspaceList } from "@/components/WorkspaceList";

export default function GlobalWorkspacesPage() {
  return (
    <RoleGuard role="admin">
      <WorkspaceList scope="global" />
    </RoleGuard>
  );
}
