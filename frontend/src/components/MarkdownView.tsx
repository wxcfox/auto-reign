import ReactMarkdown from "react-markdown";

type MarkdownViewProps = {
  content: string;
};

export function MarkdownView({ content }: MarkdownViewProps) {
  return (
    <article className="markdown-view">
      <ReactMarkdown>{content}</ReactMarkdown>
    </article>
  );
}
