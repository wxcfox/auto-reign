import { AgentManagementPage } from "@/components/AgentManagementPage";

export default async function AgentsPage({
  searchParams,
}: {
  searchParams: Promise<{ create?: string }>;
}) {
  const query = await searchParams;
  return (
    <AgentManagementPage
      initialCreate={query.create === "1"}
      scope="private"
    />
  );
}
