CREATE TABLE __THREVO_ACTIONS_SCHEMA__.proposals (
    tenant_reference text NOT NULL,
    proposal_reference text NOT NULL,
    action_namespace text NOT NULL,
    action_name text NOT NULL,
    action_version integer NOT NULL CHECK (action_version > 0),
    semantic_effect_reference text NOT NULL,
    effect_kind text NOT NULL CHECK (effect_kind IN ('single', 'itemized')),
    lifecycle_status text NOT NULL CHECK (
        lifecycle_status IN (
            'prepared', 'awaiting_authority', 'denied', 'expired', 'authorized',
            'blocked', 'stale', 'superseded', 'executing', 'failed_known',
            'failed_unknown', 'verification_pending', 'verification_unresolved',
            'partially_succeeded', 'verified', 'compensated'
        )
    ),
    revision bigint NOT NULL CHECK (revision >= 0),
    commitment_digest text,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK (expires_at > created_at),
    status_changed_at timestamptz NOT NULL,
    next_verification_at timestamptz,
    proposal_data jsonb NOT NULL CHECK (jsonb_typeof(proposal_data) = 'object'),
    PRIMARY KEY (tenant_reference, proposal_reference),
    UNIQUE (
        tenant_reference,
        proposal_reference,
        action_namespace,
        action_name,
        action_version,
        semantic_effect_reference
    ),
    UNIQUE (
        tenant_reference,
        proposal_reference,
        action_namespace,
        action_name,
        action_version,
        semantic_effect_reference,
        commitment_digest
    )
);

CREATE TABLE __THREVO_ACTIONS_SCHEMA__.authority_evidence (
    tenant_reference text NOT NULL,
    proposal_reference text NOT NULL,
    evidence_sequence bigint NOT NULL CHECK (evidence_sequence >= 0),
    action_namespace text NOT NULL,
    action_name text NOT NULL,
    action_version integer NOT NULL,
    semantic_effect_reference text NOT NULL,
    commitment_digest text NOT NULL,
    evidence_data jsonb NOT NULL CHECK (jsonb_typeof(evidence_data) = 'object'),
    CHECK (evidence_data ->> 'tenant_reference' = tenant_reference),
    CHECK (evidence_data ->> 'proposal_instance_reference' = proposal_reference),
    CHECK (evidence_data #>> '{action_type,namespace}' = action_namespace),
    CHECK (evidence_data #>> '{action_type,name}' = action_name),
    CHECK ((evidence_data #>> '{action_type,version}')::integer = action_version),
    CHECK (evidence_data ->> 'semantic_effect_reference' = semantic_effect_reference),
    CHECK (evidence_data ->> 'proposal_commitment' = commitment_digest),
    PRIMARY KEY (tenant_reference, proposal_reference, evidence_sequence),
    FOREIGN KEY (
        tenant_reference,
        proposal_reference,
        action_namespace,
        action_name,
        action_version,
        semantic_effect_reference,
        commitment_digest
    ) REFERENCES __THREVO_ACTIONS_SCHEMA__.proposals (
        tenant_reference,
        proposal_reference,
        action_namespace,
        action_name,
        action_version,
        semantic_effect_reference,
        commitment_digest
    )
);

CREATE TABLE __THREVO_ACTIONS_SCHEMA__.receipts (
    tenant_reference text NOT NULL,
    proposal_reference text NOT NULL,
    receipt_sequence bigint NOT NULL CHECK (receipt_sequence >= 0),
    receipt_reference text NOT NULL,
    receipt_data jsonb NOT NULL CHECK (jsonb_typeof(receipt_data) = 'object'),
    CHECK (receipt_data ->> 'receipt_reference' = receipt_reference),
    CHECK (receipt_data ->> 'correlation_reference' = proposal_reference),
    PRIMARY KEY (tenant_reference, proposal_reference, receipt_sequence),
    UNIQUE (tenant_reference, proposal_reference, receipt_reference),
    FOREIGN KEY (tenant_reference, proposal_reference)
        REFERENCES __THREVO_ACTIONS_SCHEMA__.proposals (
            tenant_reference,
            proposal_reference
        )
);

CREATE TABLE __THREVO_ACTIONS_SCHEMA__.effect_claims (
    tenant_reference text NOT NULL,
    action_namespace text NOT NULL,
    action_name text NOT NULL,
    action_version integer NOT NULL,
    semantic_effect_reference text NOT NULL,
    proposal_reference text NOT NULL,
    admitted_at timestamptz NOT NULL DEFAULT now(),
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
    ) REFERENCES __THREVO_ACTIONS_SCHEMA__.proposals (
        tenant_reference,
        proposal_reference,
        action_namespace,
        action_name,
        action_version,
        semantic_effect_reference
    )
);

CREATE FUNCTION __THREVO_ACTIONS_SCHEMA__.transfer_failed_known_effect_claim(
    p_tenant_reference text,
    p_action_namespace text,
    p_action_name text,
    p_action_version integer,
    p_semantic_effect_reference text,
    p_current_owner_reference text,
    p_replacement_reference text,
    p_admitted_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    UPDATE __THREVO_ACTIONS_SCHEMA__.effect_claims AS claim
    SET proposal_reference = p_replacement_reference,
        admitted_at = COALESCE(p_admitted_at, clock_timestamp())
    WHERE claim.tenant_reference = p_tenant_reference
      AND claim.action_namespace = p_action_namespace
      AND claim.action_name = p_action_name
      AND claim.action_version = p_action_version
      AND claim.semantic_effect_reference = p_semantic_effect_reference
      AND claim.proposal_reference = p_current_owner_reference
      AND EXISTS (
          SELECT 1
          FROM __THREVO_ACTIONS_SCHEMA__.proposals AS current_owner
          WHERE current_owner.tenant_reference = p_tenant_reference
            AND current_owner.proposal_reference = p_current_owner_reference
            AND current_owner.lifecycle_status = 'failed_known'
      )
      AND EXISTS (
          SELECT 1
          FROM __THREVO_ACTIONS_SCHEMA__.proposals AS replacement
          WHERE replacement.tenant_reference = p_tenant_reference
            AND replacement.proposal_reference = p_replacement_reference
            AND replacement.action_namespace = p_action_namespace
            AND replacement.action_name = p_action_name
            AND replacement.action_version = p_action_version
            AND replacement.semantic_effect_reference = p_semantic_effect_reference
            AND replacement.lifecycle_status = 'authorized'
            AND replacement.expires_at > GREATEST(
                COALESCE(p_admitted_at, '-infinity'::timestamptz),
                clock_timestamp()
            )
            AND replacement.proposal_data ->> 'erasure_pending_at' IS NULL
            AND replacement.proposal_data ->> 'erased_at' IS NULL
      );
    RETURN FOUND;
END;
$$;

REVOKE ALL ON FUNCTION __THREVO_ACTIONS_SCHEMA__.transfer_failed_known_effect_claim(
    text, text, text, integer, text, text, text, timestamptz
) FROM PUBLIC;

CREATE FUNCTION __THREVO_ACTIONS_SCHEMA__.mark_erasure_pending(
    p_tenant_reference text,
    p_proposal_reference text,
    p_expected_revision bigint,
    p_pending_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    UPDATE __THREVO_ACTIONS_SCHEMA__.proposals AS proposal
    SET revision = proposal.revision + 1,
        proposal_data = jsonb_set(
            proposal.proposal_data,
            '{erasure_pending_at}',
            to_jsonb(p_pending_at),
            true
        )
    WHERE proposal.tenant_reference = p_tenant_reference
      AND proposal.proposal_reference = p_proposal_reference
      AND proposal.revision = p_expected_revision
      AND proposal.lifecycle_status NOT IN (
          'executing', 'failed_unknown', 'verification_pending'
      )
      AND proposal.proposal_data ->> 'erasure_pending_at' IS NULL
      AND proposal.proposal_data ->> 'erased_at' IS NULL;
    RETURN FOUND;
END;
$$;

REVOKE ALL ON FUNCTION __THREVO_ACTIONS_SCHEMA__.mark_erasure_pending(
    text, text, bigint, timestamptz
) FROM PUBLIC;

CREATE FUNCTION __THREVO_ACTIONS_SCHEMA__.complete_erasure(
    p_tenant_reference text,
    p_proposal_reference text,
    p_expected_revision bigint,
    p_erased_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM 1
    FROM __THREVO_ACTIONS_SCHEMA__.proposals AS proposal
    WHERE proposal.tenant_reference = p_tenant_reference
      AND proposal.proposal_reference = p_proposal_reference
      AND proposal.revision = p_expected_revision
      AND proposal.proposal_data ->> 'erasure_pending_at' IS NOT NULL
      AND proposal.proposal_data ->> 'erased_at' IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    DELETE FROM __THREVO_ACTIONS_SCHEMA__.authority_evidence
    WHERE tenant_reference = p_tenant_reference
      AND proposal_reference = p_proposal_reference;
    DELETE FROM __THREVO_ACTIONS_SCHEMA__.receipts
    WHERE tenant_reference = p_tenant_reference
      AND proposal_reference = p_proposal_reference;

    UPDATE __THREVO_ACTIONS_SCHEMA__.proposals AS proposal
    SET revision = proposal.revision + 1,
        commitment_digest = NULL,
        next_verification_at = NULL,
        proposal_data = jsonb_set(
            jsonb_set(
                jsonb_set(
                    jsonb_set(
                        jsonb_set(
                            jsonb_set(
                                jsonb_set(
                                    jsonb_set(proposal.proposal_data, '{protected_private_snapshot}', 'null'::jsonb),
                                    '{commitment}', 'null'::jsonb
                                ),
                                '{display_preview}', '{}'::jsonb
                            ),
                            '{requesting_principal}', 'null'::jsonb
                        ),
                        '{proposing_agent}', 'null'::jsonb
                    ),
                    '{safe_result}', 'null'::jsonb
                ),
                '{execution_precondition}', 'null'::jsonb
            ),
            '{erasure_pending_at}', 'null'::jsonb
        ) || jsonb_build_object('erased_at', p_erased_at)
    WHERE proposal.tenant_reference = p_tenant_reference
      AND proposal.proposal_reference = p_proposal_reference
      AND proposal.revision = p_expected_revision;
    RETURN FOUND;
END;
$$;

REVOKE ALL ON FUNCTION __THREVO_ACTIONS_SCHEMA__.complete_erasure(
    text, text, bigint, timestamptz
) FROM PUBLIC;

CREATE INDEX proposals_due_verification_idx
    ON __THREVO_ACTIONS_SCHEMA__.proposals (
        lifecycle_status,
        next_verification_at
    )
    WHERE lifecycle_status = 'verification_pending';

CREATE FUNCTION __THREVO_ACTIONS_SCHEMA__.enforce_proposal_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    table_owner name;
BEGIN
    SELECT pg_get_userbyid(relation.relowner)
    INTO table_owner
    FROM pg_catalog.pg_class AS relation
    WHERE relation.oid = TG_RELID;

    IF (
        NEW.commitment_digest IS DISTINCT FROM OLD.commitment_digest OR
        NEW.proposal_data -> 'protected_private_snapshot'
            IS DISTINCT FROM OLD.proposal_data -> 'protected_private_snapshot' OR
        NEW.proposal_data -> 'commitment'
            IS DISTINCT FROM OLD.proposal_data -> 'commitment' OR
        NEW.proposal_data -> 'erasure_pending_at'
            IS DISTINCT FROM OLD.proposal_data -> 'erasure_pending_at' OR
        NEW.proposal_data -> 'erased_at'
            IS DISTINCT FROM OLD.proposal_data -> 'erased_at'
    ) AND current_user <> table_owner THEN
        RAISE EXCEPTION 'protected proposal state requires the retention boundary'
            USING ERRCODE = '42501';
    END IF;

    IF ROW(
        NEW.tenant_reference,
        NEW.proposal_reference,
        NEW.action_namespace,
        NEW.action_name,
        NEW.action_version,
        NEW.semantic_effect_reference,
        NEW.effect_kind,
        NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.tenant_reference,
        OLD.proposal_reference,
        OLD.action_namespace,
        OLD.action_name,
        OLD.action_version,
        OLD.semantic_effect_reference,
        OLD.effect_kind,
        OLD.created_at
    ) THEN
        RAISE EXCEPTION 'proposal identity cannot change' USING ERRCODE = '23514';
    END IF;

    IF NEW.revision <> OLD.revision + 1 THEN
        RAISE EXCEPTION 'proposal revision must advance by one' USING ERRCODE = '23514';
    END IF;

    IF NEW.lifecycle_status <> OLD.lifecycle_status AND NOT (
        (OLD.lifecycle_status = 'prepared' AND NEW.lifecycle_status = 'awaiting_authority') OR
        (OLD.lifecycle_status = 'awaiting_authority' AND NEW.lifecycle_status IN (
            'authorized', 'denied', 'expired'
        )) OR
        (OLD.lifecycle_status = 'authorized' AND NEW.lifecycle_status IN (
            'blocked', 'expired', 'executing', 'stale'
        )) OR
        (OLD.lifecycle_status = 'executing' AND NEW.lifecycle_status IN (
            'failed_known', 'failed_unknown', 'verification_pending'
        )) OR
        (OLD.lifecycle_status = 'failed_unknown' AND
            NEW.lifecycle_status = 'verification_pending') OR
        (OLD.lifecycle_status = 'verification_pending' AND NEW.lifecycle_status IN (
            'authorized', 'executing', 'failed_known', 'failed_unknown',
            'partially_succeeded', 'verification_unresolved', 'verified'
        )) OR
        (OLD.lifecycle_status = 'stale' AND NEW.lifecycle_status = 'superseded') OR
        (OLD.lifecycle_status = 'verified' AND NEW.lifecycle_status = 'compensated')
    ) THEN
        RAISE EXCEPTION 'invalid proposal lifecycle transition' USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER proposals_guarded_update
BEFORE UPDATE ON __THREVO_ACTIONS_SCHEMA__.proposals
FOR EACH ROW
EXECUTE FUNCTION __THREVO_ACTIONS_SCHEMA__.enforce_proposal_update();
