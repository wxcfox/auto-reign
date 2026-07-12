import hashlib
import json
import math
import re
from threading import Lock
from types import SimpleNamespace

from langchain_core.embeddings import Embeddings

from app.repositories.vector_store import VectorStoreUnavailable
from app.services.knowledge_chunk_service import KnowledgeChunk
from app.services.knowledge_vector_store import (
    DocumentGeneration,
    DocumentVectorScope,
    KnowledgeVectorHit,
)


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


class FakeChatStream(list[SimpleNamespace]):
    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        super().__init__(chunks)
        self.response = SimpleNamespace(
            headers={"x-request-id": "provider-request-test"}
        )
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


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

    def _stream_response(self, kwargs: dict[str, object]) -> FakeChatStream:
        content = self._chat_content(kwargs)
        split_at = max(1, len(content) // 2)
        return FakeChatStream(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content=content[:split_at])
                        )
                    ]
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content=content[split_at:])
                        )
                    ]
                ),
                SimpleNamespace(
                    choices=[],
                    usage=SimpleNamespace(
                        prompt_tokens=10,
                        completion_tokens=5,
                    ),
                ),
            ]
        )

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


class FakeKnowledgeVectorStore:
    def __init__(self) -> None:
        self.upsert_calls: list[list[KnowledgeChunk]] = []
        self.search_calls: list[tuple[str, list[DocumentGeneration], int]] = []
        self.delete_generation_calls: list[DocumentGeneration] = []
        self.delete_generations_before_calls: list[DocumentGeneration] = []
        self.delete_document_calls: list[DocumentVectorScope] = []
        self.search_results: list[KnowledgeVectorHit] = []
        self._generations: dict[
            tuple[str, int, str, int, str],
            list[KnowledgeChunk],
        ] = {}
        self._failures: dict[str, Exception] = {}
        self._partial_upsert_failure = False
        self._lock = Lock()

    def fail(
        self,
        operation: str,
        error: Exception | None = None,
    ) -> None:
        self._failures[operation] = error or VectorStoreUnavailable(
            f"fake {operation} failure"
        )

    def recover(self, operation: str | None = None) -> None:
        if operation is None:
            self._failures.clear()
            self._partial_upsert_failure = False
        else:
            self._failures.pop(operation, None)

    def fail_after_partial_upsert(self) -> None:
        self._partial_upsert_failure = True

    def upsert_generation(self, chunks: list[KnowledgeChunk]) -> None:
        self._raise_if_failed("upsert_generation")
        if not chunks:
            return
        scope = self._scope_from_chunk(chunks[0])
        if any(self._scope_from_chunk(chunk) != scope for chunk in chunks):
            raise ValueError("fake Knowledge chunks span multiple generations")
        with self._lock:
            self.upsert_calls.append(list(chunks))
            self._generations[scope] = (
                [chunks[0]] if self._partial_upsert_failure else list(chunks)
            )
        if self._partial_upsert_failure:
            self._partial_upsert_failure = False
            raise VectorStoreUnavailable("fake partial upsert failure")

    def search(
        self,
        query: str,
        *,
        scopes: list[DocumentGeneration],
        limit: int,
    ) -> list[KnowledgeVectorHit]:
        self._raise_if_failed("search")
        self.search_calls.append((query, list(scopes), limit))
        return list(self.search_results[:limit])

    def delete_generation(self, scope: DocumentGeneration) -> None:
        self._raise_if_failed("delete_generation")
        self.delete_generation_calls.append(scope)
        with self._lock:
            self._generations.pop(self._key(scope), None)

    def delete_generations_before(self, current: DocumentGeneration) -> None:
        self._raise_if_failed("delete_generations_before")
        self.delete_generations_before_calls.append(current)
        current_key = self._key(current)
        with self._lock:
            for key in list(self._generations):
                if key[:3] == current_key[:3] and key[3] < current_key[3]:
                    self._generations.pop(key, None)

    def delete_document(self, scope: DocumentVectorScope) -> None:
        self._raise_if_failed("delete_document")
        self.delete_document_calls.append(scope)
        document_key = (
            scope.collection_id,
            scope.owner_user_id,
            scope.document_id,
        )
        with self._lock:
            for key in list(self._generations):
                if key[:3] == document_key:
                    self._generations.pop(key, None)

    def has_generation(self, document_id: str, generation: int) -> bool:
        with self._lock:
            return any(
                key[2] == document_id and key[3] == generation
                for key in self._generations
            )

    def _raise_if_failed(self, operation: str) -> None:
        error = self._failures.get(operation)
        if error is not None:
            raise error

    @staticmethod
    def _key(scope: DocumentGeneration) -> tuple[str, int, str, int, str]:
        return (
            scope.collection_id,
            scope.owner_user_id,
            scope.document_id,
            scope.index_generation,
            scope.content_hash,
        )

    @classmethod
    def _scope_from_chunk(
        cls,
        chunk: KnowledgeChunk,
    ) -> tuple[str, int, str, int, str]:
        metadata = chunk.metadata
        scope = DocumentGeneration(
            collection_id=metadata["collection_id"],  # type: ignore[arg-type]
            owner_user_id=metadata["owner_user_id"],  # type: ignore[arg-type]
            document_id=metadata["document_id"],  # type: ignore[arg-type]
            index_generation=metadata["index_generation"],  # type: ignore[arg-type]
            content_hash=metadata["content_hash"],  # type: ignore[arg-type]
        )
        return cls._key(scope)
