# ruff: noqa: S101

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI

from threvo_actions.models import Money
from threvo_actions.runtime import (
    ActionOperationResult,
    AuthorizationDeniedError,
    OperationOutcome,
    ProposalNotFoundError,
)

from .app import (
    APPROVER_CREDENTIAL,
    APPROVER_IDENTITY,
    DESTINATION_VERIFIER,
    EXTRACTION_REFERENCE,
    PAYABLES_APPROVER,
    PAYMENT_CREDENTIAL,
    PAYMENT_IDENTITY,
    PAYMENT_RELEASER,
    RECEIVER_AUDIENCE,
    RECEIVER_CREDENTIAL,
    REQUESTER_CREDENTIAL,
    REQUESTER_IDENTITY,
    SUPPLIER_REFERENCE,
    TENANT_REFERENCE,
    VERIFIER_CREDENTIAL,
    VERIFIER_IDENTITY,
    SupplierDestinationExample,
    build_example,
)
from .domain import (
    BankDestination,
    DestinationChangeCommand,
    PaymentCommand,
    ReceiverDestinationResult,
    ReceiverMutationStatus,
    ReceiverState,
)
from .fake_supplier_master import SEEDED_IBAN, SEEDED_INTERNAL_SUPPLIER_ID
from .initiator_service import (
    DestinationAuthoritySubmission,
    InitiatorIdentity,
    PaymentAuthoritySubmission,
)
from .transport import (
    DestinationMutationEnvelope,
    ReceiverRejectedError,
    StateResponseEnvelope,
    SupplierMasterTransport,
)

NEW_DESTINATION = BankDestination(
    iban=SEEDED_IBAN,
    bic="ETHNGRAA",
    account_holder="Acme Components",
)
SECOND_DESTINATION = BankDestination(
    iban="FR1420041010050500013M02606",
    bic="PSSTFRPPMON",
    account_holder="Acme Components",
)


async def prepare_destination(example: SupplierDestinationExample) -> ActionOperationResult:
    return await example.application.prepare_destination(
        DestinationChangeCommand(
            supplier_reference=SUPPLIER_REFERENCE,
            extraction_reference=EXTRACTION_REFERENCE,
        ),
        REQUESTER_IDENTITY,
    )


async def record_exact_authority(
    example: SupplierDestinationExample,
    proposal_reference: str,
    *,
    identity: InitiatorIdentity = APPROVER_IDENTITY,
    destination: BankDestination = NEW_DESTINATION,
) -> ActionOperationResult:
    credential = {
        PAYABLES_APPROVER: APPROVER_CREDENTIAL,
        DESTINATION_VERIFIER: VERIFIER_CREDENTIAL,
    }[identity.principal_reference]
    transport = httpx.ASGITransport(app=example.initiator_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://initiator.local") as client:
        response = await client.post(
            f"/oob/destination/{proposal_reference}/verify",
            json={"observed_destination": destination.model_dump(mode="json")},
            headers={"Authorization": f"Bearer {credential}"},
        )
    assert response.status_code == 200
    verification_reference = response.json()
    return await example.application.record_destination_authority(
        proposal_reference,
        DestinationAuthoritySubmission(verification_reference=verification_reference),
        identity,
    )


async def approve_destination(
    example: SupplierDestinationExample,
    proposal_reference: str,
    *,
    destination: BankDestination = NEW_DESTINATION,
) -> None:
    first = await record_exact_authority(
        example,
        proposal_reference,
        identity=APPROVER_IDENTITY,
        destination=destination,
    )
    second = await record_exact_authority(
        example,
        proposal_reference,
        identity=VERIFIER_IDENTITY,
        destination=destination,
    )
    assert first.outcome is OperationOutcome.AUTHORITY_PENDING
    assert second.outcome is OperationOutcome.AUTHORIZED


async def complete_destination(
    example: SupplierDestinationExample,
    *,
    destination: BankDestination = NEW_DESTINATION,
) -> tuple[ActionOperationResult, ActionOperationResult]:
    prepared = await prepare_destination(example)
    await approve_destination(example, prepared.proposal_reference, destination=destination)
    accepted = await example.application.execute_destination(
        prepared.proposal_reference, REQUESTER_IDENTITY
    )
    verified = await example.application.reconcile_destination(
        prepared.proposal_reference, REQUESTER_IDENTITY
    )
    assert accepted.outcome is OperationOutcome.VERIFICATION_PENDING
    assert verified.outcome is OperationOutcome.VERIFIED
    return prepared, verified


def test_happy_path_keeps_raw_extraction_out_of_command_and_generic_surfaces() -> None:
    async def scenario() -> None:
        example = await build_example()
        prepared, verified = await complete_destination(example)
        private = await example.master.private_record(
            tenant_reference=TENANT_REFERENCE,
            supplier_reference=SUPPLIER_REFERENCE,
        )
        command_schema = DestinationChangeCommand.model_json_schema()

        assert set(command_schema["properties"]) == {
            "supplier_reference",
            "extraction_reference",
        }
        assert "iban" not in json.dumps(command_schema).lower()
        assert prepared.display_preview == {
            "summary": "Change supplier payment destination after independent verification",
            "masked_destination": "••••0695",
        }
        assert SEEDED_IBAN not in prepared.model_dump_json()
        assert verified.safe_result == {
            "supplier_reference": SUPPLIER_REFERENCE,
            "verified_destination_version": 2,
            "status": "verified_destination",
        }
        assert private.destination == NEW_DESTINATION
        assert private.destination_version == private.verified_destination_version == 2

    asyncio.run(scenario())


def test_duplicate_authenticated_principal_does_not_satisfy_distinct_authority() -> None:
    async def scenario() -> None:
        example = await build_example()
        prepared = await prepare_destination(example)

        first = await record_exact_authority(example, prepared.proposal_reference)
        replay = await record_exact_authority(example, prepared.proposal_reference)
        refused = await example.application.execute_destination(
            prepared.proposal_reference, REQUESTER_IDENTITY
        )

        assert first.outcome is OperationOutcome.AUTHORITY_PENDING
        assert replay.outcome is OperationOutcome.REPLAYED
        assert refused.outcome is OperationOutcome.AUTHORITY_PENDING
        assert example.transport.destination_submit_calls == 0

    asyncio.run(scenario())


def test_extraction_candidate_drift_stales_and_invalidates_unrecorded_oob_reference() -> None:
    async def scenario() -> None:
        example = await build_example()
        prepared = await prepare_destination(example)
        await approve_destination(example, prepared.proposal_reference)
        await example.extractions.register(
            tenant_reference=TENANT_REFERENCE,
            extraction_reference=EXTRACTION_REFERENCE,
            supplier_reference=SUPPLIER_REFERENCE,
            destination=SECOND_DESTINATION,
        )

        stale = await example.application.execute_destination(
            prepared.proposal_reference, REQUESTER_IDENTITY
        )
        assert stale.outcome is OperationOutcome.STALE
        assert stale.fresh_proposal_reference is not None
        assert example.transport.destination_submit_calls == 0

        fresh = await prepare_destination(example)
        verification = await example.application.register_oob_verification(
            fresh.proposal_reference,
            APPROVER_IDENTITY,
            observed_destination=SECOND_DESTINATION,
        )
        await example.extractions.register(
            tenant_reference=TENANT_REFERENCE,
            extraction_reference=EXTRACTION_REFERENCE,
            supplier_reference=SUPPLIER_REFERENCE,
            destination=NEW_DESTINATION,
        )
        with pytest.raises(LookupError):
            await example.application.record_destination_authority(
                fresh.proposal_reference,
                DestinationAuthoritySubmission(verification_reference=verification),
                APPROVER_IDENTITY,
            )

    asyncio.run(scenario())


def test_receiver_authenticates_tenant_and_caller_and_rejects_wrong_audience() -> None:
    async def scenario() -> None:
        example = await build_example()
        before = await example.master.private_record(
            tenant_reference=TENANT_REFERENCE,
            supplier_reference=SUPPLIER_REFERENCE,
        )
        request = DestinationMutationEnvelope(
            message_reference="message:untrusted",
            audience="service:someone-else",
            semantic_effect_reference="destination-change:test",
            request_binding="binding:untrusted",
            supplier_reference=SUPPLIER_REFERENCE,
            expected_supplier_version=1,
            destination=NEW_DESTINATION,
        )
        transport = httpx.ASGITransport(app=example.receiver_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://supplier-master.local",
        ) as client:
            wrong_audience = await client.post(
                "/application-v0/destination-changes",
                json=request.model_dump(mode="json"),
                headers={"Authorization": f"Bearer {RECEIVER_CREDENTIAL}"},
            )
            valid_body = request.model_copy(update={"audience": RECEIVER_AUDIENCE}).model_dump(
                mode="json"
            )
            bad_auth = await client.post(
                "/application-v0/destination-changes",
                json=valid_body,
                headers={"Authorization": "Bearer forged"},
            )
            forged_tenant = await client.post(
                "/application-v0/destination-changes",
                json={**valid_body, "tenant_reference": "tenant:forged"},
                headers={"Authorization": f"Bearer {RECEIVER_CREDENTIAL}"},
            )
        after = await example.master.private_record(
            tenant_reference=TENANT_REFERENCE,
            supplier_reference=SUPPLIER_REFERENCE,
        )

        assert wrong_audience.status_code == 403
        assert bad_auth.status_code == 401
        assert forged_tenant.status_code == 422
        assert after == before
        assert all(
            SEEDED_IBAN not in response.text
            for response in (wrong_audience, bad_auth, forged_tenant)
        )

    asyncio.run(scenario())


def test_initiator_rejects_body_identity_and_binds_oob_reference_to_authentication() -> None:
    async def scenario() -> None:
        example = await build_example()
        prepared = await prepare_destination(example)
        transport = httpx.ASGITransport(app=example.initiator_app)
        path = f"/actions/destination/{prepared.proposal_reference}/authority"
        oob_path = f"/oob/destination/{prepared.proposal_reference}/verify"
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://initiator.local",
        ) as client:
            forged_registration = await client.post(
                oob_path,
                json={
                    "observed_destination": NEW_DESTINATION.model_dump(mode="json"),
                    "principal_reference": DESTINATION_VERIFIER,
                    "tenant_reference": "tenant:forged",
                },
                headers={"Authorization": f"Bearer {APPROVER_CREDENTIAL}"},
            )
            valid_registration = await client.post(
                oob_path,
                json={"observed_destination": NEW_DESTINATION.model_dump(mode="json")},
                headers={"Authorization": f"Bearer {APPROVER_CREDENTIAL}"},
            )
            verification = valid_registration.json()
            forged_body = await client.post(
                path,
                json={
                    "verification_reference": verification,
                    "principal_reference": DESTINATION_VERIFIER,
                    "tenant_reference": "tenant:forged",
                },
                headers={"Authorization": f"Bearer {APPROVER_CREDENTIAL}"},
            )
            stolen_reference = await client.post(
                path,
                json={"verification_reference": verification},
                headers={"Authorization": f"Bearer {VERIFIER_CREDENTIAL}"},
            )
            recorded = await client.post(
                path,
                json={"verification_reference": verification},
                headers={"Authorization": f"Bearer {APPROVER_CREDENTIAL}"},
            )

        assert forged_registration.status_code == 422
        assert valid_registration.status_code == 200
        assert forged_body.status_code == 422
        assert stolen_reference.status_code == 403
        assert recorded.status_code == 200
        assert recorded.json()["outcome"] == OperationOutcome.AUTHORITY_PENDING.value
        record = await example.application.store.get(TENANT_REFERENCE, prepared.proposal_reference)
        assert record is not None
        assert [item.authority.reference for item in record.authority_evidence] == [
            PAYABLES_APPROVER
        ]

    asyncio.run(scenario())


def test_receiver_mutation_race_fails_the_atomic_precondition() -> None:
    async def scenario() -> None:
        example = await build_example()
        prepared = await prepare_destination(example)
        await approve_destination(example, prepared.proposal_reference)
        before = await example.master.private_record(
            tenant_reference=TENANT_REFERENCE,
            supplier_reference=SUPPLIER_REFERENCE,
        )
        example.master.mutate_at_next_destination_commit = True

        result = await example.application.execute_destination(
            prepared.proposal_reference, REQUESTER_IDENTITY
        )
        after = await example.master.private_record(
            tenant_reference=TENANT_REFERENCE,
            supplier_reference=SUPPLIER_REFERENCE,
        )

        assert result.outcome is OperationOutcome.FAILED_KNOWN
        assert result.reason_code == "supplier_version_changed"
        assert after.destination == before.destination
        assert after.supplier_version == before.supplier_version + 1

    asyncio.run(scenario())


def test_extraction_change_during_resolution_is_caught_before_receiver_mutation() -> None:
    async def scenario() -> None:
        example = await build_example()
        prepared = await prepare_destination(example)
        await approve_destination(example, prepared.proposal_reference)

        async def change_extraction_after_state_resolution() -> None:
            await example.extractions.register(
                tenant_reference=TENANT_REFERENCE,
                extraction_reference=EXTRACTION_REFERENCE,
                supplier_reference=SUPPLIER_REFERENCE,
                destination=SECOND_DESTINATION,
            )

        example.transport.after_next_state = change_extraction_after_state_resolution

        refused = await example.application.execute_destination(
            prepared.proposal_reference,
            REQUESTER_IDENTITY,
        )
        current = await example.master.private_record(
            tenant_reference=TENANT_REFERENCE,
            supplier_reference=SUPPLIER_REFERENCE,
        )

        assert refused.outcome is OperationOutcome.FAILED_KNOWN
        assert refused.reason_code == "extraction_version_changed"
        assert example.transport.destination_submit_calls == 0
        assert current.destination != NEW_DESTINATION

    asyncio.run(scenario())


def test_unrelated_same_tenant_principal_cannot_trigger_an_authorized_action() -> None:
    async def scenario() -> None:
        example = await build_example()
        prepared = await prepare_destination(example)
        await approve_destination(example, prepared.proposal_reference)
        path = f"/actions/destination/{prepared.proposal_reference}/execute"
        transport = httpx.ASGITransport(app=example.initiator_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://initiator.local",
        ) as client:
            denied = await client.post(
                path,
                headers={"Authorization": f"Bearer {PAYMENT_CREDENTIAL}"},
            )
            accepted = await client.post(
                path,
                headers={"Authorization": f"Bearer {REQUESTER_CREDENTIAL}"},
            )

        assert denied.status_code == 403
        assert accepted.status_code == 200
        assert accepted.json()["outcome"] == OperationOutcome.VERIFICATION_PENDING.value
        assert example.transport.destination_submit_calls == 1

    asyncio.run(scenario())


def test_timeout_after_acceptance_recovers_by_query_without_resend() -> None:
    async def scenario() -> None:
        example = await build_example()
        prepared = await prepare_destination(example)
        await approve_destination(example, prepared.proposal_reference)
        example.transport.timeout_after_next_destination_acceptance = True

        unknown = await example.application.execute_destination(
            prepared.proposal_reference, REQUESTER_IDENTITY
        )
        verified = await example.application.reconcile_destination(
            prepared.proposal_reference, REQUESTER_IDENTITY
        )
        replay = await example.application.execute_destination(
            prepared.proposal_reference, REQUESTER_IDENTITY
        )

        assert unknown.outcome is OperationOutcome.FAILED_UNKNOWN
        assert verified.outcome is OperationOutcome.VERIFIED
        assert replay.outcome is OperationOutcome.VERIFIED
        assert example.transport.destination_submit_calls == 1

    asyncio.run(scenario())


def test_destination_verifier_rejects_a_misrouted_authoritative_result() -> None:
    async def scenario() -> None:
        example = await build_example()
        prepared = await prepare_destination(example)
        await approve_destination(example, prepared.proposal_reference)
        await example.application.execute_destination(
            prepared.proposal_reference,
            REQUESTER_IDENTITY,
        )
        example.transport.misroute_next_destination_query(
            ReceiverDestinationResult(
                status=ReceiverMutationStatus.ACCEPTED,
                supplier_reference="supplier:other",
                verified_destination_version=999,
            )
        )

        refused = await example.application.reconcile_destination(
            prepared.proposal_reference,
            REQUESTER_IDENTITY,
        )

        assert refused.outcome is OperationOutcome.VERIFICATION_PENDING
        assert refused.reason_code == "receiver_binding_mismatch"
        assert refused.safe_result is not None
        assert refused.safe_result["supplier_reference"] == SUPPLIER_REFERENCE
        assert refused.safe_result["verified_destination_version"] == 2

    asyncio.run(scenario())


def test_receiver_rejects_reusing_one_effect_identity_for_different_arguments() -> None:
    async def scenario() -> None:
        example = await build_example()
        effect_reference = "destination-change:receiver-idempotency-proof"
        accepted = await example.master.change_destination(
            tenant_reference=TENANT_REFERENCE,
            supplier_reference=SUPPLIER_REFERENCE,
            expected_supplier_version=1,
            destination=NEW_DESTINATION,
            semantic_effect_reference=effect_reference,
            request_binding="binding:original",
        )
        conflict = await example.master.change_destination(
            tenant_reference=TENANT_REFERENCE,
            supplier_reference=SUPPLIER_REFERENCE,
            expected_supplier_version=1,
            destination=SECOND_DESTINATION,
            semantic_effect_reference=effect_reference,
            request_binding="binding:changed",
        )
        current = await example.master.private_record(
            tenant_reference=TENANT_REFERENCE,
            supplier_reference=SUPPLIER_REFERENCE,
        )

        assert accepted.status is ReceiverMutationStatus.ACCEPTED
        assert conflict.status is ReceiverMutationStatus.PRECONDITION_FAILED
        assert conflict.reason_code == "target_idempotency_conflict"
        assert current.destination == NEW_DESTINATION

    asyncio.run(scenario())


def test_transport_rejects_an_uncorrelated_receiver_response() -> None:
    async def scenario() -> None:
        receiver = FastAPI()

        @receiver.post("/application-v0/state")
        async def wrong_response() -> StateResponseEnvelope:
            return StateResponseEnvelope(
                message_reference="response:another-request",
                audience="service:initiator-test",
                state=ReceiverState(
                    supplier_reference=SUPPLIER_REFERENCE,
                    supplier_version=1,
                    verified_destination_version=1,
                ),
            )

        transport = SupplierMasterTransport(
            receiver,
            receiver_audience=RECEIVER_AUDIENCE,
            response_audience="service:initiator-test",
            credential="credential:test",
        )
        with pytest.raises(ReceiverRejectedError):
            await transport.state(
                supplier_reference=SUPPLIER_REFERENCE,
                message_reference="message:expected",
            )

    asyncio.run(scenario())


def test_payment_rejects_destination_evidence_and_binds_verified_destination_version() -> None:
    async def scenario() -> None:
        example = await build_example()
        destination, _ = await complete_destination(example)
        destination_record = await example.application.store.get(
            TENANT_REFERENCE, destination.proposal_reference
        )
        assert destination_record is not None
        destination_evidence = destination_record.authority_evidence[0]

        payment = await example.application.prepare_payment(
            PaymentCommand(
                payment_reference="invoice:2026-0042",
                supplier_reference=SUPPLIER_REFERENCE,
                amount=Money(amount=Decimal("1250.00"), currency="EUR"),
                verified_destination_version=2,
            ),
            REQUESTER_IDENTITY,
        )
        with pytest.raises(ProposalNotFoundError):
            await example.application.record_payment_evidence(
                destination_evidence,
                proposal_reference=payment.proposal_reference,
            )
        authorized = await example.application.record_payment_authority(
            payment.proposal_reference,
            PaymentAuthoritySubmission(),
            PAYMENT_IDENTITY,
        )
        accepted = await example.application.execute_payment(
            payment.proposal_reference, REQUESTER_IDENTITY
        )
        verified = await example.application.reconcile_payment(
            payment.proposal_reference, REQUESTER_IDENTITY
        )

        assert authorized.outcome is OperationOutcome.AUTHORIZED
        assert accepted.outcome is OperationOutcome.VERIFICATION_PENDING
        assert verified.outcome is OperationOutcome.VERIFIED
        assert verified.safe_result is not None
        assert verified.safe_result["verified_destination_version"] == 2

        await example.extractions.register(
            tenant_reference=TENANT_REFERENCE,
            extraction_reference=EXTRACTION_REFERENCE,
            supplier_reference=SUPPLIER_REFERENCE,
            destination=SECOND_DESTINATION,
        )
        await complete_destination(example, destination=SECOND_DESTINATION)
        with pytest.raises(AuthorizationDeniedError):
            await example.application.prepare_payment(
                PaymentCommand(
                    payment_reference="invoice:2026-0043",
                    supplier_reference=SUPPLIER_REFERENCE,
                    amount=Money(amount=Decimal("10.00"), currency="EUR"),
                    verified_destination_version=2,
                ),
                REQUESTER_IDENTITY,
            )

    asyncio.run(scenario())


def test_payment_verifier_rejects_a_wrong_authoritative_request_binding() -> None:
    async def scenario() -> None:
        example = await build_example()
        await complete_destination(example)
        payment = await example.application.prepare_payment(
            PaymentCommand(
                payment_reference="invoice:binding-check",
                supplier_reference=SUPPLIER_REFERENCE,
                amount=Money(amount=Decimal("500.00"), currency="EUR"),
                verified_destination_version=2,
            ),
            REQUESTER_IDENTITY,
        )
        await example.application.record_payment_authority(
            payment.proposal_reference,
            PaymentAuthoritySubmission(),
            PAYMENT_IDENTITY,
        )
        await example.application.execute_payment(
            payment.proposal_reference,
            REQUESTER_IDENTITY,
        )
        example.transport.replace_next_query_binding("binding:for-another-payment")

        refused = await example.application.reconcile_payment(
            payment.proposal_reference,
            REQUESTER_IDENTITY,
        )

        assert refused.outcome is OperationOutcome.VERIFICATION_PENDING
        assert refused.reason_code == "receiver_binding_mismatch"
        assert refused.safe_result is not None
        assert refused.safe_result["supplier_reference"] == SUPPLIER_REFERENCE
        assert refused.safe_result["verified_destination_version"] == 2

    asyncio.run(scenario())


def test_safe_422_and_generic_evidence_do_not_leak_private_values() -> None:
    async def scenario() -> None:
        example = await build_example()
        prepared, verified = await complete_destination(example)
        record = await example.application.store.get(TENANT_REFERENCE, prepared.proposal_reference)
        assert record is not None
        transport = httpx.ASGITransport(app=example.initiator_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://initiator.local",
        ) as client:
            raw_destination_attempt = await client.post(
                "/actions/destination/prepare",
                json={
                    "supplier_reference": SUPPLIER_REFERENCE,
                    "extracted_destination": {
                        "iban": SEEDED_IBAN,
                        "bic": "ETHNGRAA",
                        "account_holder": "Acme Components",
                    },
                    "internal_id": SEEDED_INTERNAL_SUPPLIER_ID,
                },
                headers={"Authorization": f"Bearer {REQUESTER_CREDENTIAL}"},
            )
            forged_payment_principal = await client.post(
                "/actions/payment/proposal:missing/authority",
                json={"principal_reference": PAYMENT_RELEASER},
                headers={"Authorization": f"Bearer {PAYMENT_CREDENTIAL}"},
            )

        assert raw_destination_attempt.status_code == 422
        assert forged_payment_principal.status_code == 422
        generic_corpus = "\n".join(
            (
                prepared.model_dump_json(),
                verified.model_dump_json(),
                record.model_dump_json(),
                raw_destination_attempt.text,
                forged_payment_principal.text,
            )
        )
        assert SEEDED_IBAN not in generic_corpus
        assert SEEDED_INTERNAL_SUPPLIER_ID not in generic_corpus
        assert RECEIVER_AUDIENCE in generic_corpus

    asyncio.run(scenario())
