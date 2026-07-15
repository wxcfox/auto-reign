import { KnowledgeCollectionList } from "@/components/KnowledgeCollectionList";
import { RoleGuard } from "@/components/RoleGuard";

export default function GlobalKnowledgePage() {
  return (
    <RoleGuard role="admin">
      <KnowledgeCollectionList scope="global" />
    </RoleGuard>
  );
}
