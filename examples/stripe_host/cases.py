"""Host-owned Stripe recovery cases and operator authorization."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from threvo_actions.integrations.stripe import (
    StripeCaseAcknowledgement,
    StripeEffectObservation,
    StripeRecoveryCase,
)
from threvo_actions.integrations.stripe.conformance import StripeHostActionGroup
from threvo_actions.migrations import quote_schema_name

if TYPE_CHECKING:
    import asyncpg

    from threvo_actions import ReadContext


class StripeCaseAuthorization(Protocol):
    async def can_observe(self, *, tenant_reference: str, operator_reference: str) -> bool: ...

    async def can_acknowledge(self, *, tenant_reference: str, operator_reference: str) -> bool: ...


class StripeEffectObserver(Protocol):
    async def observe_effect(
        self, proposal_reference: str, *, context: ReadContext
    ) -> StripeEffectObservation: ...


class StripeCaseRepository(Protocol):
    async def append(
        self,
        *,
        tenant_reference: str,
        case_reference: str,
        observation_reference: str,
        observation: StripeEffectObservation,
    ) -> None: ...

    async def acknowledge(
        self,
        *,
        tenant_reference: str,
        case_reference: str,
        acknowledgement: StripeCaseAcknowledgement,
    ) -> bool: ...

    async def load(
        self, tenant_reference: str, case_reference: str
    ) -> StripeRecoveryCase | None: ...


class StripeRecoveryCaseService:
    """Authorizes case operations and obtains every observation from the provider."""

    def __init__(
        self,
        *,
        repository: StripeCaseRepository,
        authorization: StripeCaseAuthorization,
        observers: dict[StripeHostActionGroup, StripeEffectObserver],
    ) -> None:
        self._repository = repository
        self._authorization = authorization
        self._observers = observers

    async def observe(
        self,
        *,
        tenant_reference: str,
        case_reference: str,
        operator_reference: str,
        context: ReadContext,
    ) -> StripeEffectObservation:
        if not await self._authorization.can_observe(
            tenant_reference=tenant_reference,
            operator_reference=operator_reference,
        ):
            raise LookupError("recovery case not found")
        recovery_case = await self._repository.load(tenant_reference, case_reference)
        if recovery_case is None or context.tenant_reference != tenant_reference:
            raise LookupError("recovery case not found")
        observer = self._observers.get(recovery_case.action_group)
        if observer is None:
            raise RuntimeError("recovery observer is not configured")
        observation = await observer.observe_effect(
            recovery_case.proposal_reference,
            context=context,
        )
        if (
            observation.proposal_reference != recovery_case.proposal_reference
            or observation.semantic_effect_reference != recovery_case.semantic_effect_reference
        ):
            raise RuntimeError("recovery observation binding is inconsistent")
        await self._repository.append(
            tenant_reference=tenant_reference,
            case_reference=case_reference,
            observation_reference=(
                "observation:" + hashlib.sha256(observation.model_dump_json().encode()).hexdigest()
            ),
            observation=observation,
        )
        return observation

    async def acknowledge(
        self,
        *,
        tenant_reference: str,
        case_reference: str,
        operator_reference: str,
        acknowledged_at: datetime,
    ) -> bool:
        if not await self._authorization.can_acknowledge(
            tenant_reference=tenant_reference,
            operator_reference=operator_reference,
        ):
            raise LookupError("recovery case not found")
        return await self._repository.acknowledge(
            tenant_reference=tenant_reference,
            case_reference=case_reference,
            acknowledgement=StripeCaseAcknowledgement(
                operator_reference=operator_reference,
                acknowledged_at=acknowledged_at,
            ),
        )


class PostgresStripeCaseRepository:
    def __init__(
        self,
        pool: asyncpg.Pool[asyncpg.Record],
        *,
        schema: str = "stripe_host_app",
    ) -> None:
        self._pool = pool
        self._schema = quote_schema_name(schema)

    async def open(self, case: StripeRecoveryCase) -> None:
        query = f"""INSERT INTO {self._schema}.recovery_cases (
                tenant_reference, case_reference, proposal_reference,
                semantic_effect_reference, action_group, opened_at, retain_until
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (tenant_reference, case_reference) DO NOTHING"""  # noqa: S608 -- schema is strictly validated
        await self._pool.execute(
            query,
            case.tenant_reference,
            case.case_reference,
            case.proposal_reference,
            case.semantic_effect_reference,
            case.action_group.value,
            case.opened_at,
            case.retain_until,
        )

    async def append(
        self,
        *,
        tenant_reference: str,
        case_reference: str,
        observation_reference: str,
        observation: StripeEffectObservation,
    ) -> None:
        query = f"""INSERT INTO {self._schema}.recovery_case_observations (
                tenant_reference, case_reference, observation_reference,
                observed_at, observation_data
            )
            SELECT $1, $2, $3, $4, convert_from($5::bytea, 'UTF8')::jsonb
            FROM {self._schema}.recovery_cases AS recovery_case
            WHERE recovery_case.tenant_reference = $1
              AND recovery_case.case_reference = $2
              AND recovery_case.proposal_reference = $6
              AND recovery_case.semantic_effect_reference = $7
              AND recovery_case.action_group = $8
              AND recovery_case.retain_until > clock_timestamp()
            ON CONFLICT (tenant_reference, case_reference, observation_reference)
            DO NOTHING"""  # noqa: S608 -- schema is strictly validated
        result = await self._pool.execute(
            query,
            tenant_reference,
            case_reference,
            observation_reference,
            observation.observed_at,
            observation.model_dump_json().encode(),
            observation.proposal_reference,
            observation.semantic_effect_reference,
            observation.action_group.value,
        )
        if result not in {"INSERT 0 1", "INSERT 0 0"}:
            raise RuntimeError("recovery observation could not be recorded")

    async def acknowledge(
        self,
        *,
        tenant_reference: str,
        case_reference: str,
        acknowledgement: StripeCaseAcknowledgement,
    ) -> bool:
        query = f"""INSERT INTO {self._schema}.recovery_case_acknowledgements (
                tenant_reference, case_reference, operator_reference, acknowledged_at
            ) SELECT $1, $2, $3, $4
              FROM {self._schema}.recovery_cases AS recovery_case
             WHERE recovery_case.tenant_reference = $1
               AND recovery_case.case_reference = $2
               AND recovery_case.retain_until > clock_timestamp()
            ON CONFLICT (tenant_reference, case_reference) DO NOTHING"""  # noqa: S608 -- schema is strictly validated
        result = await self._pool.execute(
            query,
            tenant_reference,
            case_reference,
            acknowledgement.operator_reference,
            acknowledgement.acknowledged_at,
        )
        if str(result) == "INSERT 0 1":
            return True
        existing_query = f"""SELECT operator_reference, acknowledged_at
            FROM {self._schema}.recovery_case_acknowledgements
            WHERE tenant_reference = $1 AND case_reference = $2"""  # noqa: S608 -- schema is strictly validated
        existing = await self._pool.fetchrow(existing_query, tenant_reference, case_reference)
        return (
            existing is not None
            and str(existing["operator_reference"]) == acknowledgement.operator_reference
            and _datetime(existing["acknowledged_at"]) == acknowledgement.acknowledged_at
        )

    async def load(self, tenant_reference: str, case_reference: str) -> StripeRecoveryCase | None:
        case_query = f"""SELECT proposal_reference, semantic_effect_reference,
                action_group, opened_at, retain_until
            FROM {self._schema}.recovery_cases
            WHERE tenant_reference = $1 AND case_reference = $2"""  # noqa: S608 -- schema is strictly validated
        observation_query = f"""SELECT convert_to(observation_data::text, 'UTF8')
            FROM {self._schema}.recovery_case_observations
            WHERE tenant_reference = $1 AND case_reference = $2
            ORDER BY observed_at, observation_reference"""  # noqa: S608 -- schema is strictly validated
        acknowledgement_query = f"""SELECT operator_reference, acknowledged_at
            FROM {self._schema}.recovery_case_acknowledgements
            WHERE tenant_reference = $1 AND case_reference = $2"""  # noqa: S608 -- schema is strictly validated
        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(case_query, tenant_reference, case_reference)
            if row is None:
                return None
            observations = await connection.fetch(
                observation_query, tenant_reference, case_reference
            )
            acknowledgement = await connection.fetchrow(
                acknowledgement_query, tenant_reference, case_reference
            )
        return StripeRecoveryCase(
            case_reference=case_reference,
            tenant_reference=tenant_reference,
            proposal_reference=str(row["proposal_reference"]),
            semantic_effect_reference=str(row["semantic_effect_reference"]),
            action_group=StripeHostActionGroup(str(row["action_group"])),
            opened_at=_datetime(row["opened_at"]),
            retain_until=_datetime(row["retain_until"]),
            observations=tuple(
                StripeEffectObservation.model_validate_json(item[0]) for item in observations
            ),
            acknowledgement=(
                None
                if acknowledgement is None
                else StripeCaseAcknowledgement(
                    operator_reference=str(acknowledgement["operator_reference"]),
                    acknowledged_at=_datetime(acknowledgement["acknowledged_at"]),
                )
            ),
        )


def _datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise RuntimeError("recovery case timestamp is corrupt")
    return value
