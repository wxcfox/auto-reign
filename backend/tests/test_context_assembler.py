from app.services.context_assembler import ContextAssembler


def test_context_assembler_keeps_direct_context_first_and_applies_budget() -> None:
    assembler = ContextAssembler(max_characters=80)

    context = assembler.assemble(
        direct_context=["[候选人画像]\nJava 后端"],
        project_context=["[项目材料]\n订单缓存项目" * 5],
        retrieved_context=["[检索片段]\nRedis 缓存击穿"],
    )

    assert context[0].startswith("[候选人画像]")
    assert sum(len(item) for item in context) <= 80
