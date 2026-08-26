from __future__ import annotations

from dataclasses import dataclass

from backend.agent import CustomerSupportAgent
from backend.auth import LoginRateLimiter, SessionTokenService
from backend.config import Settings
from backend.data_import import import_assessment_data
from backend.database import Database
from backend.documents import DocumentChunk, load_document_chunks
from backend.embeddings import EmbeddingError, FastEmbedBGE
from backend.llm import GroqStructuredLLM
from backend.policy_engine import PolicyEngine
from backend.retrieval import AuthorityAwareRetriever, ChromaSemanticStore, RetrievalError
from backend.tools.actions import CustomerActionTool
from backend.tools.customer_data import CustomerDataTool
from backend.tools.document_search import DocumentSearchTool
from backend.tools.registry import AgentTools


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    db: Database
    token_service: SessionTokenService
    login_limiter: LoginRateLimiter
    chunks: list[DocumentChunk]
    retriever: AuthorityAwareRetriever
    tools: AgentTools
    agent: CustomerSupportAgent

    @classmethod
    def create(cls, settings: Settings) -> AppContainer:
        db = Database(settings.database_path)
        if not settings.database_path.exists():
            import_assessment_data(db, settings.workbook_path, settings.users_seed_path)
        else:
            db.initialize()
        snapshot = db.metadata("dataset_snapshot")
        if snapshot is None:
            imported = import_assessment_data(db, settings.workbook_path, settings.users_seed_path)
            snapshot = imported["dataset_snapshot"]

        chunks = load_document_chunks(settings.source_docs_path, settings.registry_path)
        store = None
        # Do not trigger a model download during an ordinary server start when the
        # index has never been bootstrapped. Bootstrap is the explicit setup step.
        if settings.vector_path.exists() and any(settings.vector_path.iterdir()):
            try:
                embedder = FastEmbedBGE(settings.embedding_model, settings.embedding_cache_dir)
                candidate = ChromaSemanticStore(settings.vector_path, embedder)
                if candidate.ready(len(chunks)):
                    store = candidate
            except (EmbeddingError, RetrievalError):
                store = None
        retriever = AuthorityAwareRetriever(chunks, store)
        policies = PolicyEngine(settings.rules_path, snapshot)
        tools = AgentTools(
            document_search=DocumentSearchTool(retriever),
            customer_data=CustomerDataTool(db, policies),
            customer_action=CustomerActionTool(db, settings.action_ttl_minutes),
        )
        llm = None
        if settings.llm_provider == "groq" and settings.groq_api_key:
            llm = GroqStructuredLLM(
                settings.groq_api_key,
                settings.groq_base_url,
                settings.groq_chat_model,
            )
        agent = CustomerSupportAgent(tools=tools, llm=llm, allow_safe_fallback=settings.allow_safe_llm_fallback)
        return cls(
            settings=settings,
            db=db,
            token_service=SessionTokenService(settings.session_secret, settings.session_ttl_minutes),
            login_limiter=LoginRateLimiter(settings.login_rate_limit_per_minute),
            chunks=chunks,
            retriever=retriever,
            tools=tools,
            agent=agent,
        )

    @property
    def vector_ready(self) -> bool:
        return self.retriever.store is not None and self.retriever.store.ready(len(self.chunks))
