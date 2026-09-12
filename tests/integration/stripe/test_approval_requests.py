from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("asyncpg")
pytest.importorskip("stripe._stripe_client")

from examples.stripe_refunds.web import create_app  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from tests.integration.stripe.test_app import application, command  # noqa: E402

from threvo_actions import (  # noqa: E402
    ActionOperationResult,
    AuthorityDecision,
    AuthorityEvidence,
    ConfirmingAuthority,
    OperationOutcome,
)


def test_authenticated_request_and_callback_bind_server_owned_fields() -> None:
    async def scenario() -> None:
        async with application() as (service, gateway):
            app = create_app(service, run_worker=False)
            requester = {"Authorization": "Bearer " + "r" * 32}
            approver = {"Authorization": "Bearer " + "a" * 32}
            other = {"Authorization": "Bearer " + "o" * 32}
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                prepared = await client.post(
                    "/api/proposals",
                    headers=requester,
                    json=command().model_dump(mode="json"),
                )
                proposal = prepared.json()["proposal_reference"]
                body = {
                    "proposal_reference": proposal,
                    "intended_authority": "user:approver",
                }
                created = await client.post(
                    "/api/approval-requests", headers=requester, json=body
                )
                assert created.status_code == 200
                approval = created.json()
                repeated = await client.post(
                    "/api/approval-requests", headers=requester, json=body
                )
                assert repeated.json()["request_reference"] == approval["request_reference"]
                forged = {
                    **body,
                    "tenant_reference": "tenant:other",
                    "proposal_commitment": "forged",
                }
                assert (
                    await client.post(
                        "/api/approval-requests", headers=requester, json=forged
                    )
                ).status_code == 422
                path = f"/api/approval-requests/{approval['request_reference']}"
                assert (await client.get(path, headers=approver)).status_code == 200
                assert (await client.get(path, headers=requester)).status_code == 409
                assert (await client.get(path, headers=other)).status_code == 409

                callback = await client.post(
                    f"{path}/decision",
                    headers=approver,
                    json={"decision": "approve"},
                )
                assert callback.status_code == 200
                assert gateway.calls == 0
                replay = await client.post(
                    f"{path}/decision",
                    headers=approver,
                    json={"decision": "approve"},
                )
                assert replay.status_code == 200
                assert replay.json()["outcome"] == OperationOutcome.REPLAYED
                assert (
                    await client.post(
                        f"{path}/decision",
                        headers=approver,
                        json={"decision": "reject"},
                    )
                ).status_code == 409

    asyncio.run(scenario())


def test_lost_callback_acknowledgement_reuses_persisted_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with application() as (service, gateway):
            requester, approver, _ = service.settings.identities
            proposal = await service.prepare(requester, command())
            approval = await service.create_approval_request(
                requester, proposal.proposal_reference, approver.reference
            )
            original = service.actions.refunds.record_authority
            calls = 0

            async def lose_acknowledgement(
                evidence: AuthorityEvidence,
                *,
                authenticated_authority: ConfirmingAuthority,
            ) -> ActionOperationResult:
                nonlocal calls
                calls += 1
                await original(
                    evidence,
                    authenticated_authority=authenticated_authority,
                )
                raise RuntimeError("injected response loss")

            monkeypatch.setattr(
                service.actions.refunds,
                "record_authority",
                lose_acknowledgement,
            )
            with pytest.raises(RuntimeError, match="injected response loss"):
                await service.decide_approval_request(
                    approver,
                    approval.request_reference,
                    AuthorityDecision.APPROVE,
                )
            monkeypatch.setattr(service.actions.refunds, "record_authority", original)

            replay = await service.decide_approval_request(
                approver,
                approval.request_reference,
                AuthorityDecision.APPROVE,
            )

            assert calls == 1
            assert replay.outcome is OperationOutcome.REPLAYED
            assert gateway.calls == 0

    asyncio.run(scenario())
