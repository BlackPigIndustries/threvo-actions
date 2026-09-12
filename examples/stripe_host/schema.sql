CREATE SCHEMA IF NOT EXISTS stripe_host_app;

CREATE TABLE stripe_host_app.payments (
    tenant_reference text NOT NULL,
    payment_reference text NOT NULL,
    payment_data jsonb NOT NULL CHECK (jsonb_typeof(payment_data) = 'object'),
    PRIMARY KEY (tenant_reference, payment_reference)
);

CREATE TABLE stripe_host_app.keys (
    handle text PRIMARY KEY,
    tenant_reference text NOT NULL,
    proposal_reference text NOT NULL,
    purpose text NOT NULL,
    wrapped bytea NOT NULL
);

CREATE TABLE stripe_host_app.recovery_schedule (
    tenant_reference text NOT NULL,
    proposal_reference text NOT NULL,
    lease_token text NOT NULL,
    leased_until timestamptz NOT NULL,
    next_attempt_at timestamptz NOT NULL,
    attention_reason text,
    PRIMARY KEY (tenant_reference, proposal_reference)
);

CREATE TABLE stripe_host_app.recovery_cases (
    tenant_reference text NOT NULL,
    case_reference text NOT NULL,
    proposal_reference text NOT NULL,
    semantic_effect_reference text NOT NULL,
    action_group text NOT NULL,
    opened_at timestamptz NOT NULL,
    retain_until timestamptz NOT NULL,
    PRIMARY KEY (tenant_reference, case_reference)
);

CREATE TABLE stripe_host_app.recovery_case_observations (
    tenant_reference text NOT NULL,
    case_reference text NOT NULL,
    observation_reference text NOT NULL,
    observed_at timestamptz NOT NULL,
    observation_data jsonb NOT NULL,
    PRIMARY KEY (tenant_reference, case_reference, observation_reference),
    FOREIGN KEY (tenant_reference, case_reference)
        REFERENCES stripe_host_app.recovery_cases (tenant_reference, case_reference)
);

CREATE TABLE stripe_host_app.recovery_case_acknowledgements (
    tenant_reference text NOT NULL,
    case_reference text NOT NULL,
    operator_reference text NOT NULL,
    acknowledged_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_reference, case_reference),
    FOREIGN KEY (tenant_reference, case_reference)
        REFERENCES stripe_host_app.recovery_cases (tenant_reference, case_reference)
);

CREATE OR REPLACE FUNCTION stripe_host_app.guard_reserved_payment()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM threvo_stripe.intents
        WHERE tenant_reference = OLD.tenant_reference
          AND resource_reference = 'payment:' || OLD.payment_reference
          AND phase = 'reserved'
    ) THEN
        RAISE EXCEPTION 'payment has an unresolved Stripe reservation'
            USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER stripe_reserved_payment
BEFORE UPDATE OR DELETE ON stripe_host_app.payments
FOR EACH ROW EXECUTE FUNCTION stripe_host_app.guard_reserved_payment();
