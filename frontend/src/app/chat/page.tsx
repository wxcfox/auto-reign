import { ChatWorkspace } from "@/components/ChatWorkspace";

export default async function ChatPage({
  searchParams,
}: {
  searchParams?: Promise<{ task?: string }>;
}) {
  const params = await searchParams;
  const parsed = params?.task ? Number(params.task) : null;
  const taskId = parsed !== null && Number.isSafeInteger(parsed) && parsed > 0
    ? parsed
    : undefined;
  return <ChatWorkspace taskId={taskId} />;
}
