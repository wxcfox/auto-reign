import { InterviewWorkspace } from "@/components/InterviewWorkspace";

type InterviewPageProps = {
  searchParams?: Promise<{
    session?: string;
  }>;
};

export default async function InterviewPage({ searchParams }: InterviewPageProps) {
  const params = await searchParams;
  return <InterviewWorkspace sessionId={params?.session} />;
}
