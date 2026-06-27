import { LearningWorkspace } from "@/components/LearningWorkspace";

export default async function LearnPage({
  searchParams,
}: {
  searchParams?: Promise<{ session?: string }>;
}) {
  const params = await searchParams;
  return <LearningWorkspace sessionId={params?.session} />;
}
