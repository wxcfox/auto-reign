from __future__ import annotations

import re


def markdown_sections(markdown: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in markdown.splitlines():
        heading = re.match(r"^##\s+(.+)$", line.strip())
        if heading:
            current = heading.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {heading: "\n".join(lines).strip() for heading, lines in sections.items()}


def markdown_list_items(markdown: str, *, unique: bool = False, limit: int | None = None) -> list[str]:
    items: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        stripped = re.sub(r"^(?:[-*]|\d+[.)])\s+", "", stripped).strip()
        if stripped:
            items.append(stripped)
    if unique or limit is not None:
        return unique_items(items, limit=limit)
    return items


def replace_or_append_h2(body: str, heading: str, content: str) -> str:
    normalized = content.strip()
    replacement = f"## {heading}\n\n{normalized}\n"
    pattern = re.compile(
        rf"^##[ \t]+{re.escape(heading)}[ \t]*\n.*?(?=^##[ \t]+|\Z)",
        flags=re.DOTALL | re.MULTILINE,
    )
    if pattern.search(body):
        return pattern.sub(replacement, body, count=1)
    prefix = body.rstrip()
    return f"{prefix}\n\n{replacement}" if prefix else replacement


def unique_items(items: list[str], *, limit: int | None = None) -> list[str]:
    compact: list[str] = []
    seen: set[str] = set()
    for item in items:
        stripped = item.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        compact.append(stripped)
        if limit is not None and len(compact) >= limit:
            break
    return compact


def plain_bullet_list(items: list[str], *, empty: str = "暂无。", limit: int | None = None) -> str:
    compact = unique_items(items, limit=limit)
    if not compact:
        return f"- {empty}"
    return "\n".join(f"- {item}" for item in compact)


def indented_text(value: str, *, empty: str = "（空内容）") -> str:
    lines = value.strip().splitlines() or [empty]
    return "\n".join(f"  {line}" if line.strip() else "" for line in lines)


def indented_bullet_list(
    items: list[str],
    *,
    empty: str = "暂无明确内容，后续练习中补充。",
    limit: int = 3,
) -> str:
    compact = unique_items(items, limit=limit)
    if not compact:
        return f"  - {empty}"
    return "\n".join(f"  - {item}" for item in compact)


def slugify(value: str, *, fallback: str = "item", max_length: int | None = None) -> str:
    slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", value).strip("-").lower()
    slug = slug or fallback
    if max_length is not None:
        return slug[:max_length].strip("-") or fallback
    return slug
