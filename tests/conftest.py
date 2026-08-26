from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.agent import CustomerSupportAgent
from backend.auth import LoginRateLimiter, SessionTokenService, authenticate_customer
from backend.config import PROJECT_ROOT, Settings
from backend.container import AppContainer
from backend.data_import import import_assessment_data
from backend.database import Database
from backend.documents import load_document_chunks
from backend.main import create_app
from backend.policy_engine import PolicyEngine
from backend.retrieval import AuthorityAwareRetriever
from backend.tools.actions import CustomerActionTool
from backend.tools.customer_data import CustomerDataTool
from backend.tools.document_search import DocumentSearchTool
from backend.tools.registry import AgentTools


@pytest.fixture(scope="session")
def test_settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    runtime = tmp_path_factory.mktemp("parcelpilot-runtime")
    return Settings(
        app_env="test",
        session_secret="test-only-session-secret-that-is-definitely-longer-than-48-characters",
        groq_api_key="",
        runtime_dir=runtime,
        data_dir=PROJECT_ROOT / "data",
        frontend_dir=PROJECT_ROOT / "frontend",
    )


@pytest.fixture(scope="session")
def container(test_settings: Settings) -> AppContainer:
    db = Database(test_settings.database_path)
    import_assessment_data(db, test_settings.workbook_path, test_settings.users_seed_path)
    chunks = load_document_chunks(test_settings.source_docs_path, test_settings.registry_path)
    retriever = AuthorityAwareRetriever(chunks, store=None)
    policies = PolicyEngine(test_settings.rules_path, db.metadata("dataset_snapshot") or "")
    tools = AgentTools(
        document_search=DocumentSearchTool(retriever),
        customer_data=CustomerDataTool(db, policies),
        customer_action=CustomerActionTool(db, test_settings.action_ttl_minutes),
    )
    agent = CustomerSupportAgent(tools=tools, llm=None, allow_safe_fallback=True)
    return AppContainer(
        settings=test_settings,
        db=db,
        token_service=SessionTokenService(test_settings.session_secret, test_settings.session_ttl_minutes),
        login_limiter=LoginRateLimiter(test_settings.login_rate_limit_per_minute),
        chunks=chunks,
        retriever=retriever,
        tools=tools,
        agent=agent,
    )


@pytest.fixture(scope="session")
def identities(container: AppContainer):
    credentials = {
        "northstar": "NorthstarDemo!2026",
        "lumenworks": "LumenDemo!2026",
        "beacon": "BeaconDemo!2026",
        "axis": "AxisDemo!2026",
    }
    return {
        username: authenticate_customer(container.db, username, password) for username, password in credentials.items()
    }


@pytest.fixture()
def client(container: AppContainer, test_settings: Settings):
    with TestClient(create_app(container=container, settings=test_settings)) as test_client:
        yield test_client
