CREATE SCHEMA IF NOT EXISTS stripe_host_app;

CREATE TABLE stripe_host_app.customer_resources (
    tenant_reference text NOT NULL,
    customer_reference text NOT NULL,
    PRIMARY KEY (tenant_reference, customer_reference)
);

CREATE TABLE stripe_host_app.payments (
    tenant_reference text NOT NULL,
    payment_reference text NOT NULL,
    customer_reference text NOT NULL,
    payment_data jsonb NOT NULL CHECK (jsonb_typeof(payment_data) = 'object'),
    PRIMARY KEY (tenant_reference, payment_reference)
);

CREATE TABLE stripe_host_app.subscriptions (
    tenant_reference text NOT NULL,
    subscription_reference text NOT NULL,
    customer_reference text NOT NULL,
    subscription_data jsonb NOT NULL CHECK (jsonb_typeof(subscription_data) = 'object'),
    PRIMARY KEY (tenant_reference, subscription_reference)
);

CREATE TABLE stripe_host_app.invoices (
    tenant_reference text NOT NULL,
    invoice_reference text NOT NULL,
    customer_reference text NOT NULL,
    invoice_data jsonb NOT NULL CHECK (jsonb_typeof(invoice_data) = 'object'),
    PRIMARY KEY (tenant_reference, invoice_reference)
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

CREATE OR REPLACE FUNCTION stripe_host_app.guard_reserved_customer_resource()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM 1 FROM stripe_host_app.customer_resources
    WHERE tenant_reference = OLD.tenant_reference
      AND customer_reference = OLD.customer_reference
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'customer resource mapping is unavailable'
            USING ERRCODE = '23503';
    END IF;
    IF EXISTS (
        SELECT 1 FROM threvo_stripe.intents
        WHERE tenant_reference = OLD.tenant_reference
          AND resource_reference = 'customer:' || OLD.customer_reference
          AND phase = 'reserved'
    ) THEN
        RAISE EXCEPTION 'customer has an unresolved Stripe reservation'
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
FOR EACH ROW EXECUTE FUNCTION stripe_host_app.guard_reserved_customer_resource();

CREATE TRIGGER stripe_reserved_subscription
BEFORE UPDATE OR DELETE ON stripe_host_app.subscriptions
FOR EACH ROW EXECUTE FUNCTION stripe_host_app.guard_reserved_customer_resource();

CREATE TRIGGER stripe_reserved_invoice
BEFORE UPDATE OR DELETE ON stripe_host_app.invoices
FOR EACH ROW EXECUTE FUNCTION stripe_host_app.guard_reserved_customer_resource();
