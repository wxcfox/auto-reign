from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin_users import router as admin_users_router
from app.api.agents import admin_router as admin_agents_router
from app.api.agents import router as agents_router
from app.api.attachments import router as attachments_router
from app.api.auth import router as auth_router
from app.api.conversations import router as conversations_router
from app.api.health import router as health_router
from app.api.knowledge_collections import (
    admin_router as admin_knowledge_collections_router,
)
from app.api.knowledge_collections import router as knowledge_collections_router
from app.api.knowledge import router as knowledge_router
from app.api.models import router as models_router
from app.api.workspaces import admin_router as admin_workspaces_router
from app.api.workspaces import router as workspaces_router
from app.core.config import get_settings
from app.core.structured_logging import configure_logging
from app.core.validation_errors import request_validation_error_handler
from app.db.session import create_engine_for_settings, make_session_factory
from app.db import models
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from app.services.agent_home_capability import AgentHomeCapabilityProvider
from app.services.agent_home_service import AgentHomeService
from app.services.agent_runtime import AgentRuntime
from app.services.attachment_runtime_loader import AttachmentRuntimeLoader
from app.services.attachment_service import AttachmentService
from app.services.bootstrap_service import bootstrap_application
from app.services.extraction_service import ExtractionService
from app.services.context_assembler import ContextAssembler
from app.services.document_operation_coordinator import DocumentOperationCoordinator
from app.services.generation_service import GenerationService
from app.services.knowledge_index_worker import (
    KnowledgeIndexWorker,
    KnowledgeWorkerStopTimeout,
)
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.services.knowledge_scope_service import KnowledgeScopeService
from app.services.knowledge_retrievers import KnowledgeRetrieverFactory
from app.services.model_service import ModelService
from app.services.platform_prompt_service import PlatformPromptService
from app.services.token_counter import RuntimeTokenCounter
from app.tools.knowledge import KnowledgeCapabilityProvider
from app.services.upload_validation_service import (
    UploadValidationService,
    default_upload_policy,
)
from app.storage.factory import build_object_store
from app.middleware.request_logging import (
    RequestLoggingMiddleware,
    unhandled_exception_handler,
)


def create_app(
    *,
    knowledge_retriever_factory_override: KnowledgeRetrieverFactory | None = None,
    start_background_workers: bool = True,
) -> FastAPI:
    settings = get_settings()
    configure_logging(level=settings.log_level)
    engine = create_engine_for_settings(settings)
    session_factory = make_session_factory(engine)
    object_store = build_object_store(settings)
    agent_home_service = AgentHomeService(
        store=object_store,
        max_file_bytes=settings.agent_home_max_file_bytes,
    )
    upload_validation_service = UploadValidationService()
    attachment_upload_policy = default_upload_policy(
        max_bytes=settings.attachment_max_bytes,
    )
    attachment_service = AttachmentService(
        session_factory=session_factory,
        store=object_store,
        max_bytes=settings.attachment_max_bytes,
        extraction=ExtractionService(
            max_parsed_chars=settings.attachment_max_parsed_chars,
            max_decompressed_bytes=settings.attachment_max_decompressed_bytes,
            max_pdf_pages=settings.attachment_max_pdf_pages,
        ),
    )
    knowledge_retriever_factory = (
        knowledge_retriever_factory_override
        if knowledge_retriever_factory_override is not None
        else KnowledgeRetrieverFactory(settings=settings)
    )
    knowledge_extraction = ExtractionService(
        max_parsed_chars=settings.knowledge_max_parsed_chars,
        max_decompressed_bytes=settings.knowledge_max_decompressed_bytes,
        max_pdf_pages=settings.knowledge_max_pdf_pages,
    )
    knowledge_document_coordinator = DocumentOperationCoordinator()
    knowledge_worker = KnowledgeIndexWorker(
        session_factory=session_factory,
        repository=KnowledgeDocumentRepository(),
        object_store=object_store,
        extraction=knowledge_extraction,
        retriever_factory=knowledge_retriever_factory,
        coordinator=knowledge_document_coordinator,
        clock=models._now,
        processing_timeout=timedelta(
            seconds=settings.knowledge_worker_processing_timeout_seconds
        ),
        poll_interval=settings.knowledge_worker_poll_interval_seconds,
    )
    model_service = ModelService(settings=settings)
    attachment_runtime_loader = AttachmentRuntimeLoader(object_store=object_store)
    prompt_service = PlatformPromptService()
    token_counter = RuntimeTokenCounter(
        image_input_token_reserve=settings.image_input_token_reserve,
    )
    context_assembler = ContextAssembler(
        token_budget=settings.chat_context_token_budget,
        token_counter=token_counter,
    )
    knowledge_scope_service = KnowledgeScopeService()
    knowledge_retrieval_service = KnowledgeRetrievalService(
        object_store=object_store,
        retriever_factory=knowledge_retriever_factory,
        token_counter=token_counter,
        max_results=settings.knowledge_max_results,
        max_query_chars=settings.knowledge_max_query_chars,
        max_parsed_chars=settings.knowledge_max_parsed_chars,
    )
    agent_home_provider = AgentHomeCapabilityProvider(
        service=agent_home_service,
        token_counter=token_counter,
    )
    knowledge_provider = KnowledgeCapabilityProvider(
        scope_service=knowledge_scope_service,
        retrieval=knowledge_retrieval_service,
    )
    runtime = AgentRuntime(
        model_service=model_service,
        prompt_service=prompt_service,
        attachment_loader=attachment_runtime_loader,
        context_assembler=context_assembler,
        agent_home=agent_home_service,
        token_counter=token_counter,
        tool_result_token_reserve=settings.tool_result_token_reserve,
        capability_providers=(agent_home_provider, knowledge_provider),
    )
    runtime.configure_max_tool_rounds(settings.runtime_max_tool_rounds)
    generation_service = GenerationService(
        session_factory=session_factory,
        runtime=runtime,
        settings=settings,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        worker_started = False
        try:
            bootstrap_application(
                session_factory,
                init_data_dir=settings.init_data_dir,
            )
            generation_service.recover_interrupted()
            if start_background_workers:
                knowledge_worker.start()
                worker_started = True
            yield
        finally:
            try:
                if worker_started:
                    try:
                        knowledge_worker.stop(timeout=5)
                    except KnowledgeWorkerStopTimeout:
                        # Never dispose the engine while an in-flight Worker can
                        # still open a transaction. Wait for a bounded external
                        # operation to return, then propagate the timeout.
                        knowledge_worker.stop(timeout=None)
                        raise
            finally:
                engine.dispose()

    app = FastAPI(title="Auto Reign API", lifespan=lifespan)
    app.add_exception_handler(
        RequestValidationError,
        request_validation_error_handler,
    )
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.generation_service = generation_service
    app.state.agent_runtime = runtime
    app.state.attachment_runtime_loader = attachment_runtime_loader
    app.state.context_assembler = context_assembler
    app.state.object_store = object_store
    app.state.agent_home_service = agent_home_service
    app.state.upload_validation_service = upload_validation_service
    app.state.attachment_upload_policy = attachment_upload_policy
    app.state.attachment_service = attachment_service
    app.state.knowledge_retriever_factory = knowledge_retriever_factory
    app.state.knowledge_extraction = knowledge_extraction
    app.state.knowledge_document_coordinator = knowledge_document_coordinator
    app.state.knowledge_worker = knowledge_worker
    app.state.knowledge_scope_service = knowledge_scope_service
    app.state.knowledge_retrieval_service = knowledge_retrieval_service
    app.state.start_background_workers = start_background_workers
    app.include_router(auth_router)
    app.include_router(health_router)
    app.include_router(attachments_router)
    app.include_router(models_router)
    app.include_router(admin_users_router)
    app.include_router(agents_router)
    app.include_router(admin_agents_router)
    app.include_router(workspaces_router)
    app.include_router(admin_workspaces_router)
    app.include_router(knowledge_collections_router)
    app.include_router(admin_knowledge_collections_router)
    app.include_router(knowledge_router)
    app.include_router(conversations_router)
    return app


app = create_app()
