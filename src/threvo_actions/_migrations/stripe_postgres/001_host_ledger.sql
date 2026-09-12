CREATE TABLE __THREVO_STRIPE_SCHEMA__.intents (
    tenant_reference text NOT NULL,
    action_group text NOT NULL CHECK (
        action_group IN ('refunds', 'subscriptions', 'credit_notes')
    ),
    effect_reference text NOT NULL,
    resource_reference text NOT NULL,
    requester_reference text NOT NULL,
    snapshot_digest text NOT NULL CHECK (length(snapshot_digest) = 64),
    snapshot_data jsonb NOT NULL CHECK (jsonb_typeof(snapshot_data) = 'object'),
    phase text NOT NULL DEFAULT 'ready' CHECK (phase IN ('ready', 'reserved', 'closed')),
    reserved_at timestamptz,
    closed_at timestamptz,
    close_kind text CHECK (close_kind IN ('no_submission', 'outcome')),
    outcome_data jsonb CHECK (outcome_data IS NULL OR jsonb_typeof(outcome_data) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_reference, action_group, effect_reference),
    CHECK ((phase = 'ready') = (reserved_at IS NULL AND closed_at IS NULL)),
    CHECK ((phase = 'reserved') = (reserved_at IS NOT NULL AND closed_at IS NULL)),
    CHECK ((phase = 'closed') = (closed_at IS NOT NULL)),
    CHECK ((phase = 'closed') = (close_kind IS NOT NULL)),
    CHECK ((close_kind = 'outcome') = (outcome_data IS NOT NULL))
);

CREATE INDEX stripe_intents_resource_unresolved
ON __THREVO_STRIPE_SCHEMA__.intents (tenant_reference, resource_reference)
WHERE phase = 'reserved';

CREATE OR REPLACE FUNCTION __THREVO_STRIPE_SCHEMA__.enforce_intent_update()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF ROW(
        NEW.tenant_reference,
        NEW.action_group,
        NEW.effect_reference,
        NEW.resource_reference,
        NEW.requester_reference,
        NEW.snapshot_digest,
        NEW.snapshot_data,
        NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.tenant_reference,
        OLD.action_group,
        OLD.effect_reference,
        OLD.resource_reference,
        OLD.requester_reference,
        OLD.snapshot_digest,
        OLD.snapshot_data,
        OLD.created_at
    ) THEN
        RAISE EXCEPTION 'Stripe intent binding cannot change' USING ERRCODE = '23514';
    END IF;
    IF OLD.phase = 'closed' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'closed Stripe intent cannot change' USING ERRCODE = '23514';
    END IF;
    IF OLD.phase <> NEW.phase AND NOT (
        (OLD.phase = 'ready' AND NEW.phase IN ('reserved', 'closed')) OR
        (OLD.phase = 'reserved' AND NEW.phase = 'closed')
    ) THEN
        RAISE EXCEPTION 'invalid Stripe intent phase transition' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER stripe_intent_update_guard
BEFORE UPDATE ON __THREVO_STRIPE_SCHEMA__.intents
FOR EACH ROW EXECUTE FUNCTION __THREVO_STRIPE_SCHEMA__.enforce_intent_update();
