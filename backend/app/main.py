import asyncio
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
import socketio

from app.api.admin_users import router as admin_users_router
from app.api.agents import admin_router as admin_agents_router
from app.api.agents import router as agents_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.knowledge_collections import (
    admin_router as admin_knowledge_collections_router,
)
from app.api.knowledge_collections import router as knowledge_collections_router
from app.api.knowledge import router as knowledge_router
from app.api.models import router as models_router
from app.api.subtask_contexts import router as subtask_contexts_router
from app.api.tasks import router as tasks_router
from app.api.ws import WebSocketChatEmitter, register_chat_namespace
from app.api.workspaces import admin_router as admin_workspaces_router
from app.api.workspaces import router as workspaces_router
from app.core.config import get_settings
from app.core.socketio import (
    configure_socketio_manager,
    create_socketio_server,
    shutdown_socketio_server,
)
from app.core.structured_logging import configure_logging
from app.core.validation_errors import request_validation_error_handler
from app.db.session import create_engine_for_settings, make_session_factory
from app.db import models
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from app.services.agent_home_service import AgentHomeService
from app.services.agent_home_capability import AgentHomeCapabilityProvider
from app.services.agent_runtime import AgentRuntime
from app.services.bootstrap_service import bootstrap_application
from app.services.chat_stream_store import build_chat_realtime
from app.services.context_assembler import ContextAssembler
from app.services.extraction_service import ExtractionService
from app.services.document_operation_coordinator import DocumentOperationCoordinator
from app.services.knowledge_index_worker import (
    KnowledgeIndexWorker,
    KnowledgeWorkerStopTimeout,
)
from app.services.knowledge_retrievers import KnowledgeRetrieverFactory
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.services.knowledge_scope_service import KnowledgeScopeService
from app.services.model_service import ModelService
from app.services.platform_prompt_service import PlatformPromptService
from app.services.subtask_context_service import SubtaskContextService
from app.services.task_execution_service import TaskExecutionService
from app.services.task_service import TaskService
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
    subtask_context_service = SubtaskContextService(
        session_factory=session_factory,
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
    task_service = TaskService()
    model_service = ModelService(settings=settings)
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
        context_assembler=context_assembler,
        agent_home=agent_home_service,
        token_counter=token_counter,
        tool_result_token_reserve=settings.tool_result_token_reserve,
        capability_providers=(agent_home_provider, knowledge_provider),
    )
    runtime.configure_max_tool_rounds(settings.runtime_max_tool_rounds)
    task_execution_service = TaskExecutionService(
        session_factory=session_factory,
        runtime=runtime,
        contexts=subtask_context_service,
        settings=settings,
    )
    socket_server = create_socketio_server(settings, redis_available=False)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        worker_started = False
        realtime_backend = None
        try:
            bootstrap_application(
                session_factory,
                init_data_dir=settings.init_data_dir,
            )
            task_execution_service.recover_interrupted()
            realtime_backend = await build_chat_realtime(settings)
            configure_socketio_manager(
                socket_server,
                settings,
                redis_available=realtime_backend.redis_available,
            )
            _app.state.chat_realtime_backend = realtime_backend
            _app.state.chat_realtime = realtime_backend
            _app.state.chat_stream_store = realtime_backend.stream_store
            _app.state.chat_emitter = WebSocketChatEmitter(
                sio=socket_server,
                stream_store=realtime_backend.stream_store,
            )
            await _app.state.chat_namespace.startup()
            if start_background_workers:
                knowledge_worker.start()
                worker_started = True
            yield
        finally:
            async def cleanup() -> None:
                try:
                    await _app.state.chat_namespace.shutdown()
                finally:
                    try:
                        if worker_started:
                            try:
                                knowledge_worker.stop(timeout=5)
                            except KnowledgeWorkerStopTimeout:
                                # Never dispose the engine while an in-flight Worker
                                # can still open a transaction. Wait for the bounded
                                # external operation, then propagate the timeout.
                                knowledge_worker.stop(timeout=None)
                                raise
                    finally:
                        try:
                            await shutdown_socketio_server(socket_server)
                        finally:
                            try:
                                if realtime_backend is not None:
                                    await realtime_backend.aclose()
                            finally:
                                engine.dispose()

            await _await_cleanup_completion(cleanup())

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
    app.state.object_store = object_store
    app.state.agent_home_service = agent_home_service
    app.state.upload_validation_service = upload_validation_service
    app.state.attachment_upload_policy = attachment_upload_policy
    app.state.subtask_context_service = subtask_context_service
    app.state.context_service = subtask_context_service
    app.state.task_service = task_service
    app.state.task_execution_service = task_execution_service
    app.state.agent_runtime = runtime
    app.state.context_assembler = context_assembler
    app.state.chat_realtime_backend = None
    app.state.chat_realtime = None
    app.state.chat_stream_store = None
    app.state.chat_emitter = None
    app.state.knowledge_retriever_factory = knowledge_retriever_factory
    app.state.knowledge_extraction = knowledge_extraction
    app.state.knowledge_document_coordinator = knowledge_document_coordinator
    app.state.knowledge_worker = knowledge_worker
    app.state.knowledge_scope_service = knowledge_scope_service
    app.state.knowledge_retrieval_service = knowledge_retrieval_service
    app.state.start_background_workers = start_background_workers
    app.include_router(auth_router)
    app.include_router(health_router)
    app.include_router(subtask_contexts_router)
    app.include_router(models_router)
    app.include_router(admin_users_router)
    app.include_router(agents_router)
    app.include_router(admin_agents_router)
    app.include_router(workspaces_router)
    app.include_router(admin_workspaces_router)
    app.include_router(knowledge_collections_router)
    app.include_router(admin_knowledge_collections_router)
    app.include_router(knowledge_router)
    app.include_router(tasks_router)
    register_chat_namespace(socket_server, app)
    return app


async def _await_cleanup_completion(
    cleanup: Coroutine[object, object, None],
) -> None:
    """Drain ordered cleanup before propagating caller cancellation."""
    task = asyncio.create_task(cleanup)
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
        except BaseException as error:
            if cancellation is None:
                raise
            cancellation.add_note(
                f"lifespan cleanup failed with {type(error).__name__}"
            )
            raise cancellation from None
    if cancellation is not None:
        try:
            task.result()
        except BaseException as error:
            cancellation.add_note(
                f"lifespan cleanup failed with {type(error).__name__}"
            )
        raise cancellation
    task.result()


fastapi_app = create_app()
sio = fastapi_app.state.socket_server
app = socketio.ASGIApp(
    sio,
    other_asgi_app=fastapi_app,
    socketio_path="socket.io",
)
