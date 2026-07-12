import { AdminUserManagementPage } from "@/components/AdminUserManagementPage";
import { RoleGuard } from "@/components/RoleGuard";

export default function AdminUsersPage() {
  return (
    <RoleGuard role="admin">
      <AdminUserManagementPage />
    </RoleGuard>
  );
}
