CREATE OR REPLACE FUNCTION __THREVO_ACTIONS_SCHEMA__.enforce_proposal_update()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
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
        __THREVO_ACTIONS_LIFECYCLE_TRANSITIONS__
    ) THEN
        RAISE EXCEPTION 'invalid proposal lifecycle transition' USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;
