"""Authenticated host authorization for the maintained Stripe refund facade."""

from __future__ import annotations

from typing import TYPE_CHECKING

from threvo_actions import AuthorizationResult, ConfirmingAuthority

if TYPE_CHECKING:
    from threvo_actions import (
        AuthorityEvidence,
        DecisionContext,
        ExecutionContext,
        PreparationContext,
        ReadContext,
    )
    from threvo_actions.integrations.stripe import RefundRequest, RefundSnapshot

    from .models import Identity
    from .storage import RefundRepository


class RefundAuthorization:
    def __init__(
        self,
        *,
        identities: tuple[Identity, ...],
        repository: RefundRepository,
    ) -> None:
        self._identities = identities
        self._repository = repository

    def _permitted(self, tenant: str, principal: str, role: str | None = None) -> bool:
        return any(
            identity.tenant_reference == tenant
            and identity.reference == principal
            and (role is None or identity.role == role)
            for identity in self._identities
        )

    async def can_prepare(
        self, command: RefundRequest, *, context: PreparationContext
    ) -> AuthorizationResult:
        del command
        return AuthorizationResult(
            allowed=self._permitted(
                context.tenant_reference,
                context.requesting_principal.reference,
                "requester",
            )
        )

    async def can_decide(
        self, evidence: AuthorityEvidence, *, context: DecisionContext
    ) -> AuthorizationResult:
        record = await self._repository.intent(
            context.tenant_reference, evidence.semantic_effect_reference
        )
        return AuthorizationResult(
            allowed=self._permitted(
                context.tenant_reference, context.authority.reference, "approver"
            )
            and record.requester != context.authority.reference
        )

    async def can_execute(
        self, snapshot: RefundSnapshot, *, context: ExecutionContext
    ) -> AuthorizationResult:
        return AuthorizationResult(
            allowed=(
                snapshot.intent.tenant_reference == context.tenant_reference
                and self._permitted(
                    context.tenant_reference,
                    context.requesting_principal.reference,
                    "requester",
                )
                and bool(context.authorities)
                and all(
                    self._permitted(
                        context.tenant_reference, authority.reference, "approver"
                    )
                    and authority.reference != context.requesting_principal.reference
                    for authority in context.authorities
                )
            )
        )

    async def can_read(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference
        return self._permitted(context.tenant_reference, context.consumer.reference)


def approvers(identities: tuple[Identity, ...]) -> tuple[ConfirmingAuthority, ...]:
    return tuple(
        ConfirmingAuthority(reference=reference)
        for reference in sorted(
            {identity.reference for identity in identities if identity.role == "approver"}
        )
    )
