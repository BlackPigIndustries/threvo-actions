from __future__ import annotations

import asyncio
import base64
import inspect
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from threvo_actions.models import ActionType, LifecycleStatus
from threvo_actions.receipts import RuntimeEvent, RuntimeEventType
from threvo_actions.runtime import ActionRuntime, SystemClock, UuidIdentifiers
from threvo_actions.stores.memory import MemoryActionStore
from threvo_actions.testing import (
    EphemeralProtection,
    FixedClock,
    RecordingEventSink,
    SequentialIdentifiers,
)

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def test_runtime_supplies_production_clock_and_identifiers() -> None:
    ActionRuntime(store=MemoryActionStore())


def test_production_defaults_are_utc_and_random_uuid4() -> None:
    now = SystemClock().now()
    identifiers = UuidIdentifiers()
    first = identifiers.new("proposal")
    second = identifiers.new("proposal")

    assert now.utcoffset() == timedelta(0)
    assert first != second
    assert UUID(first.removeprefix("proposal:")).version == 4
    assert UUID(second.removeprefix("proposal:")).version == 4


def test_fixed_clock_and_sequential_identifiers_are_deterministic() -> None:
    clock = FixedClock(NOW)
    identifiers = SequentialIdentifiers()

    clock.advance(timedelta(minutes=2))

    assert clock.now() == NOW + timedelta(minutes=2)
    assert identifiers.new("proposal") == "proposal:1"
    assert identifiers.new("receipt") == "receipt:2"


def test_ephemeral_protection_requires_explicit_data_loss_acknowledgement() -> None:
    with pytest.raises(TypeError):
        inspect.signature(EphemeralProtection).bind()


def test_ephemeral_protection_round_trips_and_destroys_process_local_data() -> None:
    async def scenario() -> None:
        protection = EphemeralProtection(acknowledge_data_loss=True)
        canonical_payload = b'{"account":"private"}'

        commitment = await protection.create(
            proposal_reference="proposal:1",
            canonical_payload=canonical_payload,
        )
        payload = await protection.protect(
            proposal_reference="proposal:1",
            canonical_payload=canonical_payload,
        )

        assert await protection.verify(
            proposal_reference="proposal:1",
            canonical_payload=canonical_payload,
            commitment=commitment,
        )
        assert await protection.unprotect(payload=payload) == canonical_payload

        await protection.destroy_commitment(commitment=commitment)
        await protection.destroy_payload(payload=payload)

        assert not await protection.verify(
            proposal_reference="proposal:1",
            canonical_payload=canonical_payload,
            commitment=commitment,
        )
        with pytest.raises(KeyError):
            await protection.unprotect(payload=payload)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("codec", "corrupted-codec"),
        ("key_version", "corrupted-key-version"),
        ("ciphertext", "corrupted-ciphertext"),
    ),
)
def test_ephemeral_protection_rejects_changed_persisted_payload_fields(
    field: str,
    value: str,
) -> None:
    async def scenario() -> None:
        protection = EphemeralProtection(acknowledge_data_loss=True)
        payload = await protection.protect(
            proposal_reference="proposal:1",
            canonical_payload=b'{"account":"private"}',
        )
        corrupted = payload.model_copy(update={field: value})

        with pytest.raises(
            ValueError,
            match="protected payload metadata does not match process-local state",
        ):
            await protection.unprotect(payload=corrupted)

    asyncio.run(scenario())


def test_ephemeral_protection_ciphertext_is_random_and_not_plaintext() -> None:
    async def scenario() -> None:
        protection = EphemeralProtection(acknowledge_data_loss=True)
        canonical_payload = b'{"account":"private"}'

        first = await protection.protect(
            proposal_reference="proposal:1",
            canonical_payload=canonical_payload,
        )
        second = await protection.protect(
            proposal_reference="proposal:2",
            canonical_payload=canonical_payload,
        )

        assert first.ciphertext != second.ciphertext
        assert first.ciphertext != base64.b64encode(canonical_payload).decode("ascii")

    asyncio.run(scenario())


def test_recording_event_sink_captures_events_in_order() -> None:
    async def scenario() -> None:
        sink = RecordingEventSink()
        event = RuntimeEvent(
            event_type=RuntimeEventType.PROPOSAL_PREPARED,
            tenant_reference="tenant:test",
            proposal_reference="proposal:1",
            action_type=ActionType(namespace="example.billing", name="refund", version=1),
            lifecycle_status=LifecycleStatus.AWAITING_AUTHORITY,
            correlation_reference="proposal:1",
            observed_at=NOW,
        )

        await sink.emit(event)

        assert sink.events == [event]

    asyncio.run(scenario())
