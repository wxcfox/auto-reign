Update the fixed long-term memory files after a completed mock interview.

Return strict JSON only:

```json
{
  "weakness_summary": "string",
  "interview_summary": "string",
  "learning_profile": "string"
}
```

Rewrite only the current summary section of each file and append a dated record
to that file's history section.

Follow `language` from the request payload.
- If `language` is `zh-CN`, write all summaries in Simplified Chinese.
- Otherwise, write them in English.

The filenames are fixed:

- `weakness_memory.md`
- `interview_history.md`
- `learning_profile.md`

The headings must also follow `language`:

- `en`
  - `# Weakness Memory`, `## Current Weakness Summary`, `## Weakness History`
  - `# Interview History`, `## Current Interview Summary`, `## Interview Records`
  - `# Learning Profile`, `## Current Learning Profile`, `## Profile Updates`
- `zh-CN`
  - `# 薄弱项记忆`, `## 当前薄弱项总结`, `## 薄弱项历史记录`
  - `# 面试历史`, `## 当前面试总结`, `## 面试记录`
  - `# 学习画像`, `## 当前学习画像`, `## 画像更新记录`
