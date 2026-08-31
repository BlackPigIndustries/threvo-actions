"""Copy/paste store-conformance example using the bundled SQLite adapter."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from threvo_actions import (
    ActionType,
    ConfirmingAuthority,
    LifecycleStatus,
)
from threvo_actions.authority import AuthorityDecision, AuthorityEvidence
from threvo_actions.canonical import KeyedCommitment, ProtectedPayload
from threvo_actions.conformance import StoreConformanceCase, assert_action_store_conforms
from threvo_actions.sqlite_migrations import migrate_sqlite
from threvo_actions.stores.base import StoredProposal
from threvo_actions.stores.sqlite import SQLiteActionStore, SQLiteRetentionStore


async def check_store(database: Path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    action_type = ActionType(namespace="example.billing", name="refund", version=1)
    proposal = StoredProposal(
        tenant_reference="tenant:conformance",
        proposal_reference="proposal:conformance",
        action_type=action_type,
        semantic_effect_reference="refund:order-42",
        effect_kind="single",
        lifecycle_status=LifecycleStatus.AWAITING_AUTHORITY,
        revision=0,
        protected_private_snapshot=ProtectedPayload(
            codec="example-v1",
            key_handle="payload-key:conformance",
            key_version="1",
            ciphertext="opaque-ciphertext",
        ),
        commitment=KeyedCommitment(
            algorithm="hmac-sha256",
            key_handle="commitment-key:conformance",
            key_version="1",
            digest="opaque-digest:conformance",
        ),
        display_preview={"summary": "Refund order ORD-42"},
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        max_verification_attempts=3,
    )
    evidence = AuthorityEvidence(
        tenant_reference=proposal.tenant_reference,
        action_type=action_type,
        proposal_instance_reference=proposal.proposal_reference,
        semantic_effect_reference=proposal.semantic_effect_reference,
        authority=ConfirmingAuthority(reference="user:manager"),
        audience=("service:refunds",),
        decision=AuthorityDecision.APPROVE,
        proposal_commitment="opaque-digest:conformance",
        channel_assurance="authenticated_session",
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )

    await migrate_sqlite(database)
    await assert_action_store_conforms(
        StoreConformanceCase(
            store=SQLiteActionStore(database),
            retention_store=SQLiteRetentionStore(database),
            original=proposal,
            evidence=evidence,
            observed_at=now,
        )
    )


async def main() -> None:
    with TemporaryDirectory() as directory:
        await check_store(Path(directory) / "actions.sqlite3")
    print("SQLite ActionStore conformance: passed")


if __name__ == "__main__":
    asyncio.run(main())
