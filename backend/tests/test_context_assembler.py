from __future__ import annotations

import hashlib

from fastapi import HTTPException
import pytest

from app.services.attachment_runtime_loader import (
    RuntimeAttachment,
    RuntimeAttachmentRef,
)
from app.services.context_assembler import ContextAssembler
from app.services.runtime_types import (
    RuntimeAssistantTurn,
    RuntimeConversationTurn,
    RuntimeUserTurn,
    ToolDefinition,
)
from app.services.token_counter import RuntimeTokenCounter


def _assembler(token_budget: int) -> ContextAssembler:
    return ContextAssembler(
        token_budget=token_budget,
        token_counter=RuntimeTokenCounter(image_input_token_reserve=4_096),
    )


def _text_ref(
    attachment_id: str = "attachment-1",
    *,
    parsed_size_bytes: int = 12,
) -> RuntimeAttachmentRef:
    return RuntimeAttachmentRef(
        id=attachment_id,
        filename="笔记.txt",
        media_type="text/plain",
        source_object_key=f"users/1/attachments/{attachment_id}/source.txt",
        parsed_object_key=f"users/1/attachments/{attachment_id}/parsed.txt",
        source_size_bytes=10,
        source_content_hash=hashlib.sha256(b"source-data").hexdigest(),
        parsed_size_bytes=parsed_size_bytes,
        parsed_content_hash=hashlib.sha256(b"parsed-data").hexdigest(),
    )


def _image_ref(
    attachment_id: str = "image-1",
    *,
    size_bytes: int = 3,
) -> RuntimeAttachmentRef:
    data = b"png" if size_bytes == 3 else b"x" * size_bytes
    return RuntimeAttachmentRef(
        id=attachment_id,
        filename="diagram.png",
        media_type="image/png",
        source_object_key=f"users/1/attachments/{attachment_id}/diagram.png",
        parsed_object_key=None,
        source_size_bytes=size_bytes,
        source_content_hash=hashlib.sha256(data).hexdigest(),
        parsed_size_bytes=None,
        parsed_content_hash=None,
    )


def _turn(
    message_id: str,
    text: str,
    *,
    refs: tuple[RuntimeAttachmentRef, ...] = (),
    assistants: tuple[str, ...] = (),
) -> RuntimeConversationTurn:
    return RuntimeConversationTurn(
        user=RuntimeUserTurn(
            message_id=message_id,
            text=text,
            attachment_refs=refs,
        ),
        assistants=tuple(
            RuntimeAssistantTurn(
                message_id=f"{message_id}-assistant-{index}",
                text=answer,
            )
            for index, answer in enumerate(assistants)
        ),
    )


def test_select_turns_keeps_a_contiguous_suffix_of_atomic_user_groups() -> None:
    assembler = _assembler(180)
    old = _turn("old", "old question", assistants=("x" * 36,))
    current = _turn("current", "current question")

    selection = assembler.select_turns(
        history=(old, current),
        base_system_prompt="system",
        attachment_system_prompt=None,
    )

    assert selection.turns == (current,)
    messages = assembler.render_selected(selection, attachments={})
    assert messages == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "current question"},
    ]
    assert all(message["role"] != "assistant" for message in messages)


def test_select_turns_does_not_skip_a_nearer_oversized_group_for_an_older_one() -> None:
    assembler = _assembler(180)
    old = _turn("old", "old")
    nearer = _turn("nearer", "x" * 80)
    current = _turn("current", "current")

    selection = assembler.select_turns(
        history=(old, nearer, current),
        base_system_prompt="system",
        attachment_system_prompt=None,
    )

    assert selection.turns == (current,)


def test_current_user_group_over_budget_fails_without_partial_selection() -> None:
    assembler = _assembler(200)
    current = _turn("current", "question", assistants=("x" * 64,))

    with pytest.raises(HTTPException) as captured:
        assembler.select_turns(
            history=(current,),
            base_system_prompt="system",
            attachment_system_prompt=None,
        )

    assert captured.value.status_code == 400
    assert captured.value.detail["code"] == "context_too_large"


def test_pruned_old_attachment_does_not_select_attachment_prompt() -> None:
    assembler = _assembler(200)
    old = _turn(
        "old",
        "old",
        refs=(_text_ref(parsed_size_bytes=10_000),),
    )
    current = _turn("current", "current")

    selection = assembler.select_turns(
        history=(old, current),
        base_system_prompt="base prompt",
        attachment_system_prompt="base prompt\n\nattachment protocol",
    )

    assert selection.turns == (current,)
    assert selection.includes_attachments is False
    assert selection.platform_prompt == "base prompt"


def test_attachment_prompt_cost_can_push_an_old_attachment_group_out() -> None:
    assembler = _assembler(500)
    old = _turn(
        "old",
        "old",
        refs=(_text_ref(parsed_size_bytes=1),),
    )
    current = _turn("current", "current")

    without_extra_module_cost = assembler.select_turns(
        history=(old, current),
        base_system_prompt="b",
        attachment_system_prompt="b",
    )
    with_extra_module_cost = assembler.select_turns(
        history=(old, current),
        base_system_prompt="b",
        attachment_system_prompt="a" * 400,
    )

    assert without_extra_module_cost.turns == (old, current)
    assert with_extra_module_cost.turns == (current,)
    assert with_extra_module_cost.includes_attachments is False
    assert with_extra_module_cost.platform_prompt == "b"


def test_text_attachment_actual_size_is_bounded_by_utf8_metadata_upper_bound() -> None:
    text = "中文附件内容"
    ref = _text_ref(parsed_size_bytes=len(text.encode("utf-8")))
    assembler = _assembler(600)
    selection = assembler.select_turns(
        history=(_turn("current", "请总结", refs=(ref,), assistants=("旧回答",)),),
        base_system_prompt="base prompt",
        attachment_system_prompt="base prompt\n\nattachment protocol",
    )

    messages = assembler.render_selected(
        selection,
        attachments={
            ref.id: RuntimeAttachment(
                id=ref.id,
                filename=ref.filename,
                media_type=ref.media_type,
                text=text,
                image_bytes=None,
            )
        },
    )

    assert selection.includes_attachments is True
    assert selection.platform_prompt.endswith("attachment protocol")
    assert assembler.token_counter.count_model_input(messages, tools=()) <= (
        selection.upper_bound_tokens
    )
    assert selection.upper_bound_tokens <= selection.input_token_limit
    user_content = messages[1]["content"]
    assert isinstance(user_content, list)
    assert user_content[0] == {"type": "text", "text": "请总结"}
    assert "中文附件内容" in user_content[1]["text"]
    assert messages[2] == {"role": "assistant", "content": "旧回答"}


@pytest.mark.parametrize(
    ("image_bytes", "encoded"),
    [
        (b"x", "eA=="),
        (b"xy", "eHk="),
        (b"png", "cG5n"),
        (b"wxyz", "d3h5eg=="),
    ],
)
def test_image_is_rendered_inside_owning_user_message_with_exact_base64_bound(
    image_bytes: bytes,
    encoded: str,
) -> None:
    ref = _image_ref(size_bytes=len(image_bytes))
    assembler = _assembler(5_000)
    selection = assembler.select_turns(
        history=(_turn("current", "看图", refs=(ref,)),),
        base_system_prompt="base prompt",
        attachment_system_prompt="base prompt\n\nattachment protocol",
    )

    messages = assembler.render_selected(
        selection,
        attachments={
            ref.id: RuntimeAttachment(
                id=ref.id,
                filename=ref.filename,
                media_type=ref.media_type,
                text=None,
                image_bytes=image_bytes,
            )
        },
    )

    assert messages[1] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "看图"},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}"},
            },
        ],
    }
    assert assembler.token_counter.count_model_input(messages, tools=()) == (
        selection.upper_bound_tokens
    )


def test_tool_schema_reserve_keeps_current_turn_atomic_and_prunes_history() -> None:
    definition = ToolDefinition(
        name="read_file",
        description="Read one file.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    )
    assembler = _assembler(1_000)
    old = _turn("old", "oldest turn " + "x" * 1_000)
    current = _turn("current", "current question")

    selection = assembler.select_turns(
        history=(old, current),
        base_system_prompt="platform",
        attachment_system_prompt=None,
        agent_prompt="agent",
        agents_md="# rules",
        tool_definitions=(definition,),
        tool_result_token_reserve=300,
    )
    messages = assembler.render_selected(selection, attachments={})

    assert selection.turns == (current,)
    assert selection.input_token_limit == 700
    assert assembler.token_counter.count_model_input(
        messages,
        tools=(definition,),
    ) <= 700
    assert [message["content"] for message in messages[:3]] == [
        "platform",
        "agent",
        "# rules",
    ]


def test_current_turn_with_too_many_images_is_rejected_without_dropping_any() -> None:
    refs = tuple(_image_ref(f"image-{index}") for index in range(3))
    current = _turn("current", "inspect all", refs=refs)
    assembler = _assembler(10_000)

    with pytest.raises(HTTPException) as captured:
        assembler.select_turns(
            history=(current,),
            base_system_prompt="platform",
            attachment_system_prompt="platform\n\nattachments",
        )

    assert captured.value.detail["code"] == "context_too_large"
    assert current.user.attachment_refs == refs
