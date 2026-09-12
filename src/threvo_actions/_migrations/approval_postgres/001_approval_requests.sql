CREATE TABLE __THREVO_APPROVAL_SCHEMA__.approval_requests (
    request_reference text PRIMARY KEY,
    tenant_reference text NOT NULL,
    proposal_reference text NOT NULL,
    binding_data jsonb NOT NULL CHECK (jsonb_typeof(binding_data) = 'object'),
    decision_data jsonb CHECK (
        decision_data IS NULL OR jsonb_typeof(decision_data) = 'object'
    ),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_reference, proposal_reference)
);

CREATE OR REPLACE FUNCTION __THREVO_APPROVAL_SCHEMA__.enforce_approval_request_update()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF ROW(
        NEW.request_reference,
        NEW.tenant_reference,
        NEW.proposal_reference,
        NEW.binding_data,
        NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.request_reference,
        OLD.tenant_reference,
        OLD.proposal_reference,
        OLD.binding_data,
        OLD.created_at
    ) THEN
        RAISE EXCEPTION 'approval request binding cannot change' USING ERRCODE = '23514';
    END IF;
    IF OLD.decision_data IS NOT NULL AND NEW.decision_data IS DISTINCT FROM OLD.decision_data THEN
        RAISE EXCEPTION 'approval decision cannot change' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER approval_request_update_guard
BEFORE UPDATE ON __THREVO_APPROVAL_SCHEMA__.approval_requests
FOR EACH ROW EXECUTE FUNCTION __THREVO_APPROVAL_SCHEMA__.enforce_approval_request_update();
