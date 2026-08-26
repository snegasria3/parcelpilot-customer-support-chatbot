from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.auth import (
    COOKIE_NAME,
    AuthenticationError,
    AuthorizationError,
    ScopedRecordNotFound,
    authenticate_customer,
    identity_from_session,
    require_csrf,
)
from backend.config import Settings, get_settings
from backend.container import AppContainer
from backend.logging_config import configure_logging
from backend.schemas import (
    AccountContext,
    ActionResult,
    ChatRequest,
    ChatResponse,
    CustomerIdentity,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    SessionResponse,
)


def _account_view(row: dict) -> AccountContext:
    return AccountContext(
        account_id=row["account_id"],
        account_name=row["account_name"],
        plan=row["plan"],
        status=row["status"],
        csm=row["csm"],
        contract_file=row.get("contract_file"),
        premium_support=bool(row["premium_support"]),
    )


def create_app(container: AppContainer | None = None, settings: Settings | None = None) -> FastAPI:
    selected_settings = settings or get_settings()
    configure_logging(selected_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not hasattr(app.state, "container"):
            app.state.container = AppContainer.create(selected_settings)
        yield

    application = FastAPI(
        title="ParcelPilot Customer Support",
        version="2.0.0",
        description="Account-isolated, evidence-grounded customer support chatbot",
        lifespan=lifespan,
        docs_url="/api/docs" if selected_settings.app_env == "development" else None,
        redoc_url=None,
    )
    if container is not None:
        application.state.container = container

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def services(request: Request) -> AppContainer:
        return request.app.state.container

    def session(
        request: Request,
        current: AppContainer = Depends(services),
    ) -> tuple[CustomerIdentity, str]:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
        try:
            return identity_from_session(current.db, current.token_service, token)
        except AuthenticationError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    @application.get("/api/health", response_model=HealthResponse)
    def health(current: AppContainer = Depends(services)) -> HealthResponse:
        warnings = current.settings.warnings()
        if not current.vector_ready:
            warnings.append("Learned semantic vector index is not ready; run bootstrap --rebuild-index")
        database_ready = current.db.scalar("SELECT COUNT(*) FROM accounts") == 4
        if not database_ready:
            warnings.append("Assessment database is not ready")
        return HealthResponse(
            status="degraded" if warnings else "ok",
            database_ready=database_ready,
            vector_index_ready=current.vector_ready,
            embedding_model=current.settings.embedding_model,
            llm=current.settings.groq_chat_model if current.settings.groq_api_key else "safe_fallback",
            dataset_snapshot=current.db.metadata("dataset_snapshot"),
            warnings=warnings,
        )

    @application.get("/api/session", response_model=SessionResponse)
    def get_session(request: Request, current: AppContainer = Depends(services)) -> SessionResponse:
        demo_accounts = [
            {"username": row["username"], "account_name": row["account_name"]}
            for row in current.db.fetch_all(
                """SELECT u.username, a.account_name FROM customer_users u
                JOIN accounts a ON a.account_id = u.account_id WHERE u.is_active = 1 ORDER BY a.account_id"""
            )
        ]
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return SessionResponse(authenticated=False, demo_accounts=demo_accounts)
        try:
            identity, csrf = identity_from_session(current.db, current.token_service, token)
        except AuthenticationError:
            return SessionResponse(authenticated=False, demo_accounts=demo_accounts)
        account = current.tools.customer_data.account(identity)
        return SessionResponse(
            authenticated=True,
            user=identity,
            account=_account_view(account),
            csrf_token=csrf,
            capabilities={
                "llm": "groq" if current.settings.groq_api_key else "safe_fallback",
                "retrieval": "bge+chroma" if current.vector_ready else "required-source",
                "actions": "local_sqlite",
            },
            demo_accounts=demo_accounts,
        )

    @application.post("/api/auth/login", response_model=LoginResponse)
    def login(
        payload: LoginRequest, request: Request, response: Response, current: AppContainer = Depends(services)
    ) -> LoginResponse:
        client = request.client.host if request.client else "unknown"
        rate_key = f"{client}:{payload.username.lower()}"
        try:
            current.login_limiter.check(rate_key)
            identity = authenticate_customer(current.db, payload.username, payload.password)
            current.login_limiter.clear(rate_key)
        except AuthenticationError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        token, csrf, ttl_seconds = current.token_service.issue(identity)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=ttl_seconds,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
        return LoginResponse(
            user=identity,
            account=_account_view(current.tools.customer_data.account(identity)),
            csrf_token=csrf,
            expires_in_seconds=ttl_seconds,
        )

    @application.post("/api/auth/logout")
    def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
        return {"logged_out": True}

    @application.post("/api/chat", response_model=ChatResponse)
    def chat(
        payload: ChatRequest,
        auth: tuple[CustomerIdentity, str] = Depends(session),
        current: AppContainer = Depends(services),
    ) -> ChatResponse:
        identity, _ = auth
        if len(payload.message) > current.settings.max_message_chars:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Message is too long")
        try:
            return current.agent.run(
                message=payload.message,
                identity=identity,
                conversation_id=payload.conversation_id,
            )
        except ScopedRecordNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Record not found in your account"
            ) from exc

    @application.post("/api/actions/{action_id}/confirm", response_model=ActionResult)
    def confirm_action(
        action_id: str,
        x_csrf_token: str | None = Header(default=None),
        auth: tuple[CustomerIdentity, str] = Depends(session),
        current: AppContainer = Depends(services),
    ) -> ActionResult:
        identity, csrf = auth
        try:
            require_csrf(csrf, x_csrf_token)
            return current.tools.customer_action.confirm(identity, action_id)
        except (AuthorizationError, ScopedRecordNotFound) as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @application.post("/api/actions/{action_id}/cancel", response_model=ActionResult)
    def cancel_action(
        action_id: str,
        x_csrf_token: str | None = Header(default=None),
        auth: tuple[CustomerIdentity, str] = Depends(session),
        current: AppContainer = Depends(services),
    ) -> ActionResult:
        identity, csrf = auth
        try:
            require_csrf(csrf, x_csrf_token)
            return current.tools.customer_action.cancel(identity, action_id)
        except (AuthorizationError, ScopedRecordNotFound) as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    assets = selected_settings.frontend_dir
    application.mount("/assets", StaticFiles(directory=assets), name="assets")

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(Path(assets) / "index.html")

    return application


app = create_app()
