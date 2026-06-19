Analyze the uploaded Markdown or TXT document and return strict JSON only.

The response must match this shape:

```json
{
  "title": "string",
  "summary": "string",
  "tags": ["string"],
  "knowledge_points": ["string"],
  "weakness_candidates": ["string"]
}
```

Use concise, interview-preparation-focused language. Do not include API keys,
credentials, or unrelated personal data in generated metadata.
