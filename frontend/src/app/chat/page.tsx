import { ChatWorkspace } from "@/components/ChatWorkspace";

export default async function ChatPage({
  searchParams,
}: {
  searchParams?: Promise<{ session?: string }>;
}) {
  const params = await searchParams;
  return <ChatWorkspace sessionId={params?.session} />;
}
