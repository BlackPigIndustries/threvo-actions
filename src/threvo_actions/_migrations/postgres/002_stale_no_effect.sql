CREATE OR REPLACE FUNCTION __THREVO_ACTIONS_SCHEMA__.transfer_failed_known_effect_claim(
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
            AND current_owner.lifecycle_status IN ('failed_known', 'stale')
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

CREATE OR REPLACE FUNCTION __THREVO_ACTIONS_SCHEMA__.enforce_proposal_update()
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
            'stale', 'failed_known', 'failed_unknown', 'verification_pending'
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
