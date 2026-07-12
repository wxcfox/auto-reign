import hashlib
import json
import math
import re
from types import SimpleNamespace

from langchain_core.embeddings import Embeddings


class StableTestEmbeddings(Embeddings):
    def __init__(self, dimension: int = 32) -> None:
        self.dimension = dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        words = re.findall(r"[A-Za-z][A-Za-z0-9_-]*", text.lower())
        for word in words or [text.lower()]:
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            index = digest[0] % len(vector)
            sign = 1.0 if digest[1] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class FakeOpenAIEmbeddings(StableTestEmbeddings):
    def __init__(self, **_kwargs) -> None:
        super().__init__()


class FakeChatCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return self._stream_response(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._chat_content(kwargs)))]
        )

    def _stream_response(self, kwargs: dict[str, object]) -> list[SimpleNamespace]:
        content = self._chat_content(kwargs)
        split_at = max(1, len(content) // 2)
        return [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=content[:split_at]))]
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=content[split_at:]))]
            ),
        ]

    def _chat_content(self, kwargs: dict[str, object]) -> str:
        messages = kwargs.get("messages")
        system_prompt = ""
        payload: dict[str, object] = {}
        if isinstance(messages, list) and messages:
            first = messages[0]
            if isinstance(first, dict):
                system_prompt = str(first.get("content", ""))
            if len(messages) > 1:
                second = messages[1]
                if isinstance(second, dict):
                    try:
                        loaded = json.loads(str(second.get("content", "{}")))
                    except json.JSONDecodeError:
                        loaded = {}
                    if isinstance(loaded, dict):
                        payload = loaded
        if "learning note" in system_prompt.lower():
            text = str(payload.get("text") or "")
            topic = "MySQL" if "MySQL" in text else "Redis"
            return json.dumps(
                {
                    "title": f"{topic} 学习记录",
                    "summary": f"已整理：{text or topic}。",
                    "key_points": ["需要结合具体场景说明治理方案。"],
                    "interview_takeaways": ["先说风险，再说方案取舍。"],
                    "follow_up_questions": ["布隆过滤器误判会带来什么影响？"],
                },
                ensure_ascii=False,
            )
        if "answer" in system_prompt.lower() and "feedback" in system_prompt.lower():
            if payload.get("language") == "zh-CN":
                return (
                    '{"feedback":"回答有基本结构，可以继续补充具体取舍、失败场景和量化结果。",'
                    '"missing_points":["具体失败处理","可观测性指标"],'
                    '"follow_up_question":"在真实生产流量下，你会优先做哪些取舍？",'
                    '"weaknesses":["需要更深入的工程细节"],'
                    '"review_suggestions":["准备一个包含故障处理和指标的项目案例"],'
                    '"better_answer":"我会先说明方案边界，再结合真实流量下的失败处理、监控指标和降级预案，把技术取舍讲清楚。",'
                    '"mastery_change":"basic","should_write_weakness":true,'
                    '"should_write_high_frequency":false,'
                    '"tested_points":["工程取舍","失败处理","可观测性"]}'
                )
            return (
                '{"feedback":"The answer shows relevant structure and can be strengthened '
                'with concrete tradeoffs.","missing_points":["Concrete failure handling",'
                '"Operational metrics"],"follow_up_question":"What tradeoffs would you make '
                'under production traffic?","weaknesses":["Needs deeper operational detail"],'
                '"review_suggestions":["Prepare one concrete architecture incident example"],'
                '"better_answer":"I would start with the system boundary, then explain concrete '
                'failure handling, observability, and degradation tradeoffs.",'
                '"mastery_change":"basic","should_write_weakness":true,'
                '"should_write_high_frequency":false,'
                '"tested_points":["Tradeoffs","Failure handling","Observability"]}'
            )
        if "report" in system_prompt.lower():
            if payload.get("language") == "zh-CN":
                return (
                    '{"summary":"这是测试模型生成的复盘。","strong_signals":[],'
                    '"missing_points":[],"weaknesses":[],"review_focus":[],'
                    '"source_context":[]}'
                )
            return (
                '{"summary":"This session was generated by the test model service.",'
                '"strong_signals":[],"missing_points":[],"weaknesses":[],'
                '"review_focus":[],"source_context":[]}'
            )
        role = str(payload.get("target_role") or "").strip()
        company = str(payload.get("target_company") or "").strip()
        if payload.get("language") == "zh-CN":
            role_text = role or "这个岗位"
            company_text = company or "目标公司"
            return f"请结合你的经历，说明你会如何胜任{company_text}的{role_text}？"
        role_text = role or "Backend Engineer"
        company_text = company or "OpenAI"
        return f"How would you explain your {role_text} experience for {company_text}?"


class FakeOpenAIClient:
    def __init__(self, **_kwargs) -> None:
        self.completions = FakeChatCompletions()
        self.chat = SimpleNamespace(completions=self.completions)
