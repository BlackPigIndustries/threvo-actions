import asyncio
import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import Field, TypeAdapter, ValidationError
from pydantic_ai import DeferredToolRequests
from pydantic_ai.models import infer_model
from pydantic_ai.usage import UsageLimits

from threvo_actions import (
    ActionOperationResult,
    AuthorizationDeniedError,
    ProposalNotFoundError,
    ProposalView,
)
from threvo_actions.integrations.stripe import StripeBoundaryError, verify_refund_webhook
from threvo_actions.models import ExperimentalModel
from threvo_actions.recovery import ActionRecoveryView  # noqa: TC001

from .agent import AgentDependencies, build_agent
from .models import AppError, Identity, RefundCommand
from .service import RefundService

logger = logging.getLogger(__name__)


class Decision(ExperimentalModel):
    approve: bool


class ChatRequest(ExperimentalModel):
    message: Annotated[str, Field(min_length=1, max_length=4000)]


class ChatResponse(ExperimentalModel):
    message: str
    awaiting_approval: bool


def create_app(service: RefundService, *, run_worker: bool = True) -> FastAPI:
    async def worker() -> None:
        while True:
            try:
                await service.sweep()
            except Exception:
                logger.error("refund worker iteration failed")
            await asyncio.sleep(5)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(worker()) if run_worker else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Stripe refund operations", lifespan=lifespan)

    def identity(authorization: Annotated[str, Header()] = "") -> Identity:
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Authentication required")
        token = authorization.removeprefix("Bearer ")
        for candidate in service.settings.identities:
            if hmac.compare_digest(candidate.token.get_secret_value(), token):
                return candidate
        raise HTTPException(401, "Authentication required")

    @app.exception_handler(AppError)
    @app.exception_handler(AuthorizationDeniedError)
    @app.exception_handler(ProposalNotFoundError)
    @app.exception_handler(StripeBoundaryError)
    async def refusal(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "Operation unavailable or refused"})

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def invalid_request(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        page = Path(__file__).with_name("index.html").read_text()
        return HTMLResponse(
            page,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'unsafe-inline'; "
                    "style-src 'unsafe-inline'; frame-ancestors 'none'"
                ),
            },
        )

    @app.get("/api/session")
    async def session(who: Annotated[Identity, Depends(identity)]) -> dict[str, str]:
        return {"role": who.role, "mode": "sandbox"}

    @app.get("/api/proposals")
    async def proposals(who: Annotated[Identity, Depends(identity)]) -> tuple[ProposalView, ...]:
        return tuple(
            [
                await service.read(who, reference)
                for reference in await service.repository.proposals(who.tenant_reference)
            ]
        )

    @app.post("/api/proposals")
    async def prepare(
        request: Request, who: Annotated[Identity, Depends(identity)]
    ) -> ActionOperationResult:
        # Validate JSON directly so Decimal strings retain their exact semantics.
        command = TypeAdapter(RefundCommand).validate_json(await request.body())
        return await service.prepare(who, command)

    @app.post("/api/proposals/{proposal}/decision")
    async def decide(
        proposal: str, decision: Decision, who: Annotated[Identity, Depends(identity)]
    ) -> ActionOperationResult:
        return await service.decide(who, proposal, decision.approve)

    @app.get("/api/proposals/{proposal}/recovery")
    async def recovery(
        proposal: str, who: Annotated[Identity, Depends(identity)]
    ) -> ActionRecoveryView:
        return await service.read_recovery(who, proposal)

    @app.get("/api/cases")
    async def cases(who: Annotated[Identity, Depends(identity)]) -> list[dict[str, str]]:
        if who.role != "approver":
            raise HTTPException(403, "Approver required")
        result = []
        for effect in await service.repository.cases(who.tenant_reference):
            record = await service.repository.intent(who.tenant_reference, effect)
            result.append(
                {
                    "effect_reference": effect,
                    "order_reference": record.snapshot.payment_reference,
                }
            )
        return result

    @app.post("/api/cases/{effect}/refresh")
    async def refresh(effect: str, who: Annotated[Identity, Depends(identity)]) -> JSONResponse:
        observation = await service.refresh_case(who, effect)
        return JSONResponse(observation.model_dump(mode="json"))

    @app.post("/api/chat")
    async def chat(body: ChatRequest, who: Annotated[Identity, Depends(identity)]) -> ChatResponse:
        if who.role != "requester":
            raise HTTPException(403, "Requester required")
        if service.settings.model is None:
            raise HTTPException(503, "Configure an agent model to enable the assistant")
        agent = build_agent(
            service,
            infer_model(service.settings.model),
            include_recovery=service.settings.agent_recovery_enabled,
        )
        async with asyncio.timeout(45):
            result = await agent.run(
                body.message,
                deps=AgentDependencies(who),
                usage_limits=UsageLimits(request_limit=5, tool_calls_limit=3),
            )
        deferred = isinstance(result.output, DeferredToolRequests)
        return ChatResponse(
            message="" if deferred else str(result.output), awaiting_approval=deferred
        )

    @app.post("/webhooks/stripe")
    async def webhook(
        request: Request, stripe_signature: Annotated[str, Header()] = ""
    ) -> dict[str, bool]:
        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > 262_144:
                raise HTTPException(413, "Webhook too large")
            payload.extend(chunk)
        hint = verify_refund_webhook(
            bytes(payload), stripe_signature, service.settings.webhook_secret
        )
        if hint is not None:
            if hint.livemode:
                raise HTTPException(400, "Sandbox events required")
            await service.repository.receipt_hint(hint.event_reference)
            # Durable periodic discovery is authoritative; webhook delivery and
            # ordering never decide completion or trigger another refund request.
        return {"received": True}

    return app
