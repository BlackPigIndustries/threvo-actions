"""Authorized, minimized projections of recorded action evidence."""

from __future__ import annotations

import hashlib
import html
import json
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, JsonValue, TypeAdapter, model_serializer, model_validator

from .authority import AuthorityDecision
from .canonical import canonicalize_v1
from .models import ActionType, EffectKind, ExperimentalModel, LifecycleStatus, SafeReference
from .receipts import (
    ExecutionReceipt,
    ExecutionReceiptStatus,
    ProposalReceipt,
    ProposalReceiptStatus,
    Receipt,
    VerificationReceipt,
    VerificationReceiptStatus,
)

if TYPE_CHECKING:
    from .stores.base import StoredProposal

_JSON_OBJECT_ADAPTER: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(
    dict[str, JsonValue]
)
_JSON_VALUE_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class _FrozenJsonEntry(ExperimentalModel):
    name: Annotated[str, Field(max_length=255)]
    canonical_value: Annotated[str, Field(max_length=1_048_576)]


class FrozenJsonObject(ExperimentalModel):
    """Deeply immutable JSON object that serializes as an ordinary JSON object."""

    entries: tuple[_FrozenJsonEntry, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def freeze_serialized_object(cls, value: object) -> object:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            return value
        validated = _JSON_OBJECT_ADAPTER.validate_python(value)
        return {
            "entries": tuple(
                {
                    "name": name,
                    "canonical_value": canonicalize_v1(item).decode(),
                }
                for name, item in sorted(validated.items())
            )
        }

    @model_serializer(mode="plain")
    def serialize_object(self) -> dict[str, JsonValue]:
        return {
            entry.name: _JSON_VALUE_ADAPTER.validate_json(entry.canonical_value)
            for entry in self.entries
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, JsonValue]) -> FrozenJsonObject:
        return cls.model_validate(value)

    @property
    def is_empty(self) -> bool:
        return not self.entries


class EvidenceOmission(StrEnum):
    PRIVATE_SNAPSHOT = "private_snapshot"
    PROTECTED_PAYLOAD = "protected_payload"
    COMMITMENT_AND_CUSTODY = "commitment_and_custody"
    REPLAYABLE_AUTHORITY_EVIDENCE = "replayable_authority_evidence"
    ERASED_SOURCE_EVIDENCE = "erased_source_evidence"
    LEGACY_RUNTIME_ATTRIBUTION = "legacy_runtime_attribution"
    HISTORICAL_AUTHORITY_UNAVAILABLE = "historical_authority_unavailable"


class EvidenceAuthoritySummary(ExperimentalModel):
    """Non-replayable account of a recorded authority decision."""

    authority_reference: SafeReference
    decision: AuthorityDecision
    audience: tuple[SafeReference, ...]
    channel_assurance: SafeReference
    issued_at: datetime
    expires_at: datetime


class ActionEvidenceBundle(ExperimentalModel):
    """Unsigned export of one authorized stored proposal revision."""

    schema_version: Literal["threvo.actions.evidence/v1"] = (
        "threvo.actions.evidence/v1"
    )
    authenticity: Literal["unsigned_host_projection"] = "unsigned_host_projection"
    exported_at: datetime
    exporter_runtime_revision: SafeReference
    proposal_reference: SafeReference
    action_type: ActionType
    semantic_effect_reference: SafeReference
    effect_kind: EffectKind
    source_revision: Annotated[int, Field(ge=0)]
    lifecycle_status: LifecycleStatus
    created_at: datetime
    expires_at: datetime
    erased: bool
    display_preview: FrozenJsonObject
    safe_result: FrozenJsonObject | None = None
    authority_summaries: tuple[EvidenceAuthoritySummary, ...] = ()
    receipts: tuple[Receipt, ...] = ()
    embedded_receipt_schema_versions: tuple[SafeReference, ...] = ()
    omitted: tuple[EvidenceOmission, ...]
    content_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class EvidenceValidationStatus(StrEnum):
    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    INSUFFICIENT = "insufficient"


class EvidenceValidationReason(StrEnum):
    DIGEST_MISMATCH = "digest_mismatch"
    DUPLICATE_RECEIPT = "duplicate_receipt"
    DANGLING_RECEIPT_LINK = "dangling_receipt_link"
    RECEIPT_IDENTITY_MISMATCH = "receipt_identity_mismatch"
    RECEIPT_VERSION_MISMATCH = "receipt_version_mismatch"
    LIFECYCLE_EVIDENCE_MISMATCH = "lifecycle_evidence_mismatch"
    ERASED_SOURCE = "erased_source"
    LEGACY_ATTRIBUTION_MISSING = "legacy_attribution_missing"
    AUTHORITY_HISTORY_MISSING = "authority_history_missing"


class EvidenceValidationReport(ExperimentalModel):
    """Pure consistency result; it does not authenticate the export."""

    status: EvidenceValidationStatus
    reasons: tuple[EvidenceValidationReason, ...] = ()
    digest_matches: bool
    authenticity_established: Literal[False] = False


def _digest_payload(bundle: ActionEvidenceBundle) -> dict[str, JsonValue]:
    payload = bundle.model_dump(mode="json", exclude={"content_digest"})
    return _JSON_OBJECT_ADAPTER.validate_python(payload)


def evidence_digest(bundle: ActionEvidenceBundle) -> str:
    """Hash bundle content; authenticity requires a separately trusted digest."""

    return hashlib.sha256(canonicalize_v1(_digest_payload(bundle))).hexdigest()


def build_evidence_bundle(
    record: StoredProposal,
    *,
    exported_at: datetime,
    exporter_runtime_revision: str,
) -> ActionEvidenceBundle:
    """Project one stored revision without private state or replayable authority."""

    erased = record.erasure_pending_at is not None or record.erased_at is not None
    receipts = () if erased else record.receipts
    summaries = (
        ()
        if erased
        else tuple(
            EvidenceAuthoritySummary(
                authority_reference=evidence.authority.reference,
                decision=evidence.decision,
                audience=evidence.audience,
                channel_assurance=evidence.channel_assurance,
                issued_at=evidence.issued_at,
                expires_at=evidence.expires_at,
            )
            for evidence in record.authority_evidence
        )
    )
    omissions = [
        EvidenceOmission.PRIVATE_SNAPSHOT,
        EvidenceOmission.PROTECTED_PAYLOAD,
        EvidenceOmission.COMMITMENT_AND_CUSTODY,
        EvidenceOmission.REPLAYABLE_AUTHORITY_EVIDENCE,
    ]
    if erased:
        omissions.append(EvidenceOmission.ERASED_SOURCE_EVIDENCE)
    if any(receipt.runtime_revision is None for receipt in receipts):
        omissions.append(EvidenceOmission.LEGACY_RUNTIME_ATTRIBUTION)
    if (
        not erased
        and record.lifecycle_status is not LifecycleStatus.AWAITING_AUTHORITY
        and not summaries
    ):
        omissions.append(EvidenceOmission.HISTORICAL_AUTHORITY_UNAVAILABLE)
    bundle = ActionEvidenceBundle(
        exported_at=exported_at,
        exporter_runtime_revision=exporter_runtime_revision,
        proposal_reference=record.proposal_reference,
        action_type=record.action_type,
        semantic_effect_reference=record.semantic_effect_reference,
        effect_kind=record.effect_kind,
        source_revision=record.revision,
        lifecycle_status=record.lifecycle_status,
        created_at=record.created_at,
        expires_at=record.expires_at,
        erased=erased,
        display_preview=FrozenJsonObject.from_mapping(
            {} if erased else record.display_preview
        ),
        safe_result=(
            None
            if erased or record.safe_result is None
            else FrozenJsonObject.from_mapping(record.safe_result)
        ),
        authority_summaries=summaries,
        receipts=receipts,
        embedded_receipt_schema_versions=tuple(
            sorted({receipt.schema_version for receipt in receipts})
        ),
        omitted=tuple(omissions),
        content_digest="0" * 64,
    )
    return bundle.model_copy(update={"content_digest": evidence_digest(bundle)})


def validate_evidence_bundle(bundle: ActionEvidenceBundle) -> EvidenceValidationReport:
    """Validate internal consistency without claiming provenance or completeness."""

    inconsistent: list[EvidenceValidationReason] = []
    insufficient: list[EvidenceValidationReason] = []
    digest_matches = evidence_digest(bundle) == bundle.content_digest
    if not digest_matches:
        inconsistent.append(EvidenceValidationReason.DIGEST_MISMATCH)

    references = [receipt.receipt_reference for receipt in bundle.receipts]
    reference_set = set(references)
    if len(reference_set) != len(references):
        inconsistent.append(EvidenceValidationReason.DUPLICATE_RECEIPT)
    if any(
        link is not None and (link not in reference_set or link == receipt.receipt_reference)
        for receipt in bundle.receipts
        for link in (
            receipt.corrects_receipt_reference,
            receipt.supersedes_receipt_reference,
        )
    ):
        inconsistent.append(EvidenceValidationReason.DANGLING_RECEIPT_LINK)
    if any(
        receipt.correlation_reference != bundle.proposal_reference
        or (
            receipt.causation_reference != bundle.proposal_reference
            and receipt.causation_reference not in reference_set
        )
        for receipt in bundle.receipts
    ):
        inconsistent.append(EvidenceValidationReason.RECEIPT_IDENTITY_MISMATCH)
    versions = tuple(sorted({receipt.schema_version for receipt in bundle.receipts}))
    if versions != bundle.embedded_receipt_schema_versions or any(
        version != "internal/v0" for version in versions
    ):
        inconsistent.append(EvidenceValidationReason.RECEIPT_VERSION_MISMATCH)
    if not _lifecycle_is_supported(bundle):
        inconsistent.append(EvidenceValidationReason.LIFECYCLE_EVIDENCE_MISMATCH)

    if bundle.erased or EvidenceOmission.ERASED_SOURCE_EVIDENCE in bundle.omitted:
        insufficient.append(EvidenceValidationReason.ERASED_SOURCE)
    if EvidenceOmission.LEGACY_RUNTIME_ATTRIBUTION in bundle.omitted or any(
        receipt.runtime_revision is None for receipt in bundle.receipts
    ):
        insufficient.append(EvidenceValidationReason.LEGACY_ATTRIBUTION_MISSING)
    if EvidenceOmission.HISTORICAL_AUTHORITY_UNAVAILABLE in bundle.omitted or (
        not bundle.erased
        and bundle.lifecycle_status is not LifecycleStatus.AWAITING_AUTHORITY
        and not bundle.authority_summaries
    ):
        insufficient.append(EvidenceValidationReason.AUTHORITY_HISTORY_MISSING)

    if inconsistent:
        status = EvidenceValidationStatus.INCONSISTENT
        reasons = tuple(dict.fromkeys(inconsistent))
    elif insufficient:
        status = EvidenceValidationStatus.INSUFFICIENT
        reasons = tuple(dict.fromkeys(insufficient))
    else:
        status = EvidenceValidationStatus.CONSISTENT
        reasons = ()
    return EvidenceValidationReport(
        status=status,
        reasons=reasons,
        digest_matches=digest_matches,
    )


def _lifecycle_is_supported(bundle: ActionEvidenceBundle) -> bool:
    if bundle.erased:
        return (
            not bundle.receipts
            and not bundle.authority_summaries
            and bundle.display_preview.is_empty
            and bundle.safe_result is None
        )
    if not bundle.receipts or not isinstance(bundle.receipts[0], ProposalReceipt):
        return False
    if bundle.receipts[0].status is not ProposalReceiptStatus.PREPARED:
        return False
    if bundle.lifecycle_status is LifecycleStatus.VERIFIED:
        return any(
            isinstance(receipt, VerificationReceipt)
            and receipt.status is VerificationReceiptStatus.VERIFIED_COMPLETION
            for receipt in bundle.receipts
        )
    if bundle.lifecycle_status is LifecycleStatus.FAILED_KNOWN:
        return any(
            (
                isinstance(receipt, ExecutionReceipt)
                and receipt.status is ExecutionReceiptStatus.FAILED_KNOWN
            )
            or (
                isinstance(receipt, VerificationReceipt)
                and receipt.status
                is VerificationReceiptStatus.VERIFIED_TERMINAL_FAILURE
            )
            for receipt in bundle.receipts
        )
    return True


def render_evidence_html(bundle: ActionEvidenceBundle) -> str:
    """Render a self-contained, escaped summary for a human reviewer."""

    validation = validate_evidence_bundle(bundle)
    preview = json.dumps(bundle.display_preview.model_dump(mode="json"), indent=2)
    result = (
        "Unavailable"
        if bundle.safe_result is None
        else json.dumps(bundle.safe_result.model_dump(mode="json"), indent=2)
    )
    receipt_rows = "".join(
        "<li>"
        f"{html.escape(receipt.receipt_type)}: "
        f"{html.escape(str(receipt.status))} at "
        f"{html.escape(receipt.observed_at.isoformat())}"
        "</li>"
        for receipt in bundle.receipts
    ) or "<li>No retained receipts</li>"
    return (
        "<article>"
        "<h1>Governed action evidence</h1>"
        f"<p>Proposal: {html.escape(bundle.proposal_reference)}</p>"
        f"<p>Lifecycle: {html.escape(bundle.lifecycle_status.value)}</p>"
        f"<p>Consistency: {html.escape(validation.status.value)}</p>"
        "<p>This is an unsigned host projection. Internal consistency does not "
        "establish authenticity, completeness, or compliance.</p>"
        f"<h2>Preview</h2><pre>{html.escape(preview)}</pre>"
        f"<h2>Recorded result</h2><pre>{html.escape(result)}</pre>"
        f"<h2>Receipts</h2><ul>{receipt_rows}</ul>"
        "</article>"
    )


__all__ = [
    "ActionEvidenceBundle",
    "EvidenceAuthoritySummary",
    "EvidenceOmission",
    "EvidenceValidationReason",
    "EvidenceValidationReport",
    "EvidenceValidationStatus",
    "FrozenJsonObject",
    "build_evidence_bundle",
    "evidence_digest",
    "render_evidence_html",
    "validate_evidence_bundle",
]
