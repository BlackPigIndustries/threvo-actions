CREATE TABLE proposals (
    tenant_reference TEXT NOT NULL,
    proposal_reference TEXT NOT NULL,
    action_namespace TEXT NOT NULL,
    action_name TEXT NOT NULL,
    action_version INTEGER NOT NULL CHECK (action_version > 0),
    semantic_effect_reference TEXT NOT NULL,
    effect_kind TEXT NOT NULL CHECK (effect_kind IN ('single', 'itemized')),
    lifecycle_status TEXT NOT NULL CHECK (
        lifecycle_status IN (
            'awaiting_authority', 'denied', 'expired', 'authorized', 'blocked',
            'stale', 'superseded', 'executing', 'failed_known', 'failed_unknown',
            'verification_pending', 'verification_unresolved',
            'partially_succeeded', 'verified'
        )
    ),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    proposal_data TEXT NOT NULL CHECK (
        json_valid(proposal_data) AND json_type(proposal_data) = 'object'
    ),
    CHECK (json_extract(proposal_data, '$.tenant_reference') = tenant_reference),
    CHECK (json_extract(proposal_data, '$.proposal_reference') = proposal_reference),
    CHECK (json_extract(proposal_data, '$.action_type.namespace') = action_namespace),
    CHECK (json_extract(proposal_data, '$.action_type.name') = action_name),
    CHECK (json_extract(proposal_data, '$.action_type.version') = action_version),
    CHECK (
        json_extract(proposal_data, '$.semantic_effect_reference') =
        semantic_effect_reference
    ),
    CHECK (json_extract(proposal_data, '$.effect_kind') = effect_kind),
    CHECK (json_extract(proposal_data, '$.lifecycle_status') = lifecycle_status),
    CHECK (json_extract(proposal_data, '$.revision') = revision),
    PRIMARY KEY (tenant_reference, proposal_reference),
    UNIQUE (
        tenant_reference,
        proposal_reference,
        action_namespace,
        action_name,
        action_version,
        semantic_effect_reference
    )
) STRICT;

CREATE TABLE effect_claims (
    tenant_reference TEXT NOT NULL,
    action_namespace TEXT NOT NULL,
    action_name TEXT NOT NULL,
    action_version INTEGER NOT NULL,
    semantic_effect_reference TEXT NOT NULL,
    proposal_reference TEXT NOT NULL,
    admitted_at TEXT NOT NULL,
    PRIMARY KEY (
        tenant_reference,
        action_namespace,
        action_name,
        action_version,
        semantic_effect_reference
    ),
    FOREIGN KEY (
        tenant_reference,
        proposal_reference,
        action_namespace,
        action_name,
        action_version,
        semantic_effect_reference
    ) REFERENCES proposals (
        tenant_reference,
        proposal_reference,
        action_namespace,
        action_name,
        action_version,
        semantic_effect_reference
    )
) STRICT;

CREATE INDEX proposals_lifecycle_status_idx
    ON proposals (lifecycle_status);

CREATE TRIGGER enforce_proposal_update
BEFORE UPDATE ON proposals
BEGIN
    SELECT CASE WHEN
        NEW.tenant_reference IS NOT OLD.tenant_reference OR
        NEW.proposal_reference IS NOT OLD.proposal_reference OR
        NEW.action_namespace IS NOT OLD.action_namespace OR
        NEW.action_name IS NOT OLD.action_name OR
        NEW.action_version IS NOT OLD.action_version OR
        NEW.semantic_effect_reference IS NOT OLD.semantic_effect_reference OR
        NEW.effect_kind IS NOT OLD.effect_kind OR
        NEW.created_at IS NOT OLD.created_at
    THEN RAISE(ABORT, 'proposal identity cannot change') END;

    SELECT CASE WHEN NEW.revision <> OLD.revision + 1
    THEN RAISE(ABORT, 'proposal revision must advance by one') END;

    SELECT CASE WHEN NEW.lifecycle_status <> OLD.lifecycle_status AND NOT (
        (OLD.lifecycle_status = 'awaiting_authority' AND
            NEW.lifecycle_status = 'denied') OR
        (OLD.lifecycle_status = 'awaiting_authority' AND
            NEW.lifecycle_status = 'expired') OR
        (OLD.lifecycle_status = 'awaiting_authority' AND
            NEW.lifecycle_status = 'authorized') OR
        (OLD.lifecycle_status = 'authorized' AND
            NEW.lifecycle_status = 'expired') OR
        (OLD.lifecycle_status = 'authorized' AND
            NEW.lifecycle_status = 'blocked') OR
        (OLD.lifecycle_status = 'authorized' AND
            NEW.lifecycle_status = 'stale') OR
        (OLD.lifecycle_status = 'authorized' AND
            NEW.lifecycle_status = 'executing') OR
        (OLD.lifecycle_status = 'stale' AND
            NEW.lifecycle_status = 'superseded') OR
        (OLD.lifecycle_status = 'executing' AND
            NEW.lifecycle_status = 'stale') OR
        (OLD.lifecycle_status = 'executing' AND
            NEW.lifecycle_status = 'failed_known') OR
        (OLD.lifecycle_status = 'executing' AND
            NEW.lifecycle_status = 'failed_unknown') OR
        (OLD.lifecycle_status = 'executing' AND
            NEW.lifecycle_status = 'verification_pending') OR
        (OLD.lifecycle_status = 'failed_unknown' AND
            NEW.lifecycle_status = 'verification_pending') OR
        (OLD.lifecycle_status = 'verification_pending' AND
            NEW.lifecycle_status = 'authorized') OR
        (OLD.lifecycle_status = 'verification_pending' AND
            NEW.lifecycle_status = 'executing') OR
        (OLD.lifecycle_status = 'verification_pending' AND
            NEW.lifecycle_status = 'failed_known') OR
        (OLD.lifecycle_status = 'verification_pending' AND
            NEW.lifecycle_status = 'failed_unknown') OR
        (OLD.lifecycle_status = 'verification_pending' AND
            NEW.lifecycle_status = 'verification_unresolved') OR
        (OLD.lifecycle_status = 'verification_pending' AND
            NEW.lifecycle_status = 'partially_succeeded') OR
        (OLD.lifecycle_status = 'verification_pending' AND
            NEW.lifecycle_status = 'verified')
    ) THEN RAISE(ABORT, 'invalid proposal lifecycle transition') END;
END;
