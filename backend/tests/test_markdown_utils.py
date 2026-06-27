from app.services.markdown_utils import (
    markdown_list_items,
    markdown_sections,
    plain_bullet_list,
    replace_or_append_h2,
    slugify,
    unique_items,
)


def test_markdown_sections_collects_second_level_sections_case_insensitively() -> None:
    sections = markdown_sections(
        "# Title\n\n## Current Focus\n\n- A\n\n## 最近整理\n\n- B\n"
    )

    assert sections["current focus"] == "- A"
    assert sections["最近整理"] == "- B"


def test_markdown_list_items_accepts_markers_and_deduplicates_when_requested() -> None:
    assert markdown_list_items("- Redis\n* MySQL\n1. Redis", unique=True) == ["Redis", "MySQL"]


def test_replace_or_append_h2_replaces_existing_section_and_appends_missing_one() -> None:
    body = "# Doc\n\n## A\n\nold\n\n## B\n\nkeep\n"

    replaced = replace_or_append_h2(body, "A", "new")
    assert "## A\n\nnew\n" in replaced
    assert "## B\n\nkeep" in replaced

    appended = replace_or_append_h2(replaced, "C", "later")
    assert appended.rstrip().endswith("## C\n\nlater")


def test_unique_items_and_plain_bullet_list_keep_order() -> None:
    assert unique_items([" a ", "b", "a", "", "c"], limit=2) == ["a", "b"]
    assert plain_bullet_list([" a ", "b", "a"]) == "- a\n- b"
    assert plain_bullet_list([]) == "- 暂无。"


def test_slugify_supports_chinese_and_ascii() -> None:
    assert slugify(" Redis 缓存击穿 / Hot Key! ") == "redis-缓存击穿-hot-key"
    assert slugify("!!!", fallback="note") == "note"
