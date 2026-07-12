import { AgentManagementPage } from "@/components/AgentManagementPage";
import { RoleGuard } from "@/components/RoleGuard";

export default function GlobalAgentsPage() {
  return (
    <RoleGuard role="admin">
      <AgentManagementPage scope="global" />
    </RoleGuard>
  );
}
