from __future__ import annotations

import asyncio
import os

import pytest

from threvo_actions.conformance import assert_providers_conform
from threvo_actions.integrations.local_kek import (
    LocalKekEnvelopeProtection,
    LocalKekMaterial,
    LocalWrappedDataKey,
    LocalWrappedKeyDeleteOutcome,
)
from threvo_actions.models import ProposalIdentity


class MemoryKekProvider:
    def __init__(self) -> None:
        self.current_version = "kek-v1"
        self.keys = {"kek-v1": os.urandom(32)}

    def rotate(self) -> None:
        self.current_version = "kek-v2"
        self.keys["kek-v2"] = os.urandom(32)

    async def current(self) -> LocalKekMaterial:
        return LocalKekMaterial(
            version=self.current_version,
            key=bytearray(self.keys[self.current_version]),
        )

    async def resolve(self, version: str) -> LocalKekMaterial | None:
        key = self.keys.get(version)
        return None if key is None else LocalKekMaterial(version=version, key=bytearray(key))


class MemoryEnvelopeStore:
    def __init__(self) -> None:
        self.entries: dict[str, LocalWrappedDataKey] = {}

    async def put(self, *, key_handle: str, envelope: LocalWrappedDataKey) -> None:
        if key_handle in self.entries:
            raise ValueError("duplicate key handle")
        self.entries[key_handle] = envelope

    async def get(self, *, key_handle: str) -> LocalWrappedDataKey | None:
        return self.entries.get(key_handle)

    async def delete_if_matches(
        self,
        *,
        key_handle: str,
        proposal_identity: ProposalIdentity,
        purpose: str,
        kek_version: str,
    ) -> LocalWrappedKeyDeleteOutcome:
        envelope = self.entries.get(key_handle)
        if envelope is None:
            return LocalWrappedKeyDeleteOutcome.ALREADY_ABSENT
        if (
            envelope.tenant_reference != proposal_identity.tenant_reference
            or envelope.proposal_reference != proposal_identity.proposal_reference
            or envelope.purpose != purpose
            or envelope.kek_version != kek_version
        ):
            return LocalWrappedKeyDeleteOutcome.MISMATCH
        del self.entries[key_handle]
        return LocalWrappedKeyDeleteOutcome.DELETED


def _identity(reference: str = "proposal:local") -> ProposalIdentity:
    return ProposalIdentity(
        tenant_reference="tenant:one",
        proposal_reference=reference,
    )


def test_local_kek_protection_satisfies_provider_contract() -> None:
    async def scenario() -> None:
        envelopes = MemoryEnvelopeStore()
        protection = LocalKekEnvelopeProtection(
            keks=MemoryKekProvider(),
            envelopes=envelopes,
        )

        await assert_providers_conform(
            commitment_provider=protection,
            protection_codec=protection,
            proposal_reference="proposal:local",
            canonical_payload=b'{"private":"value"}',
            mutated_payload=b'{"private":"changed"}',
        )

        assert not envelopes.entries

    asyncio.run(scenario())


def test_old_envelopes_remain_readable_after_kek_rotation() -> None:
    async def scenario() -> None:
        keks = MemoryKekProvider()
        protection = LocalKekEnvelopeProtection(keks=keks, envelopes=MemoryEnvelopeStore())
        identity = _identity()
        payload = await protection.protect_for(
            proposal_identity=identity,
            canonical_payload=b"secret",
        )

        keks.rotate()

        assert (
            await protection.unprotect_for(
                proposal_identity=identity,
                payload=payload,
            )
            == b"secret"
        )

    asyncio.run(scenario())


def test_tenant_rebinding_and_missing_old_kek_fail_closed() -> None:
    async def scenario() -> None:
        keks = MemoryKekProvider()
        protection = LocalKekEnvelopeProtection(keks=keks, envelopes=MemoryEnvelopeStore())
        identity = _identity()
        payload = await protection.protect_for(
            proposal_identity=identity,
            canonical_payload=b"secret",
        )

        with pytest.raises(ValueError, match="metadata does not match"):
            await protection.unprotect_for(
                proposal_identity=ProposalIdentity(
                    tenant_reference="tenant:two",
                    proposal_reference=identity.proposal_reference,
                ),
                payload=payload,
            )

        del keks.keys[payload.key_version]
        with pytest.raises(KeyError):
            await protection.unprotect_for(proposal_identity=identity, payload=payload)

    asyncio.run(scenario())
