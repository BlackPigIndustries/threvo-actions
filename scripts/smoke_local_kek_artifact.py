"""Exercise local-KEK protection from an installed distribution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from threvo_actions import ProposalIdentity
from threvo_actions.integrations.local_kek import (
    EnvelopePurpose,
    LocalKekEnvelopeProtection,
    LocalKekMaterial,
    LocalWrappedDataKey,
    LocalWrappedKeyDeleteOutcome,
)


class _Keks:
    async def current(self) -> LocalKekMaterial:
        return LocalKekMaterial(version="1", key=bytearray(b"k" * 32))

    async def resolve(self, version: str) -> LocalKekMaterial | None:
        if version != "1":
            return None
        return await self.current()


@dataclass
class _Envelopes:
    values: dict[str, LocalWrappedDataKey] = field(default_factory=dict)

    async def put(self, *, key_handle: str, envelope: LocalWrappedDataKey) -> None:
        self.values[key_handle] = envelope

    async def get(self, *, key_handle: str) -> LocalWrappedDataKey | None:
        return self.values.get(key_handle)

    async def delete_if_matches(
        self,
        *,
        key_handle: str,
        proposal_identity: ProposalIdentity,
        purpose: EnvelopePurpose,
        kek_version: str,
    ) -> LocalWrappedKeyDeleteOutcome:
        current = self.values.get(key_handle)
        if current is None:
            return LocalWrappedKeyDeleteOutcome.ALREADY_ABSENT
        if (
            current.tenant_reference != proposal_identity.tenant_reference
            or current.proposal_reference != proposal_identity.proposal_reference
            or current.purpose != purpose
            or current.kek_version != kek_version
        ):
            return LocalWrappedKeyDeleteOutcome.MISMATCH
        del self.values[key_handle]
        return LocalWrappedKeyDeleteOutcome.DELETED


async def _main() -> None:
    identity = ProposalIdentity(
        tenant_reference="tenant:artifact-smoke",
        proposal_reference="proposal:artifact-smoke",
    )
    protection = LocalKekEnvelopeProtection(keks=_Keks(), envelopes=_Envelopes())
    payload = await protection.protect_for(
        proposal_identity=identity,
        canonical_payload=b'{"amount":"10.00"}',
    )
    recovered = await protection.unprotect_for(
        proposal_identity=identity,
        payload=payload,
    )
    if recovered != b'{"amount":"10.00"}':
        raise RuntimeError("local-KEK artifact round trip changed the payload")
    await protection.destroy_payload_for(proposal_identity=identity, payload=payload)


if __name__ == "__main__":
    asyncio.run(_main())
