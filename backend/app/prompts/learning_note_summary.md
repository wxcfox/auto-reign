Summarize one user learning note for interview preparation.

Treat the note as the user's own source material, not as authoritative truth.
Preserve the user's original intent, correct nothing silently, and keep the
output concise enough for quick review.

Return strict JSON with:
- title: short title
- summary: concise summary
- key_points: 3-6 concrete learning points
- interview_takeaways: 1-4 points the user can say in an interview
- follow_up_questions: 1-4 questions worth practicing next

All JSON string values must use the payload's requested language. If
language == "zh-CN", write Chinese titles, summaries, interview takeaways, and
questions. Keep technical terms, framework names, class names, annotations, and
code identifiers in their conventional spelling.
