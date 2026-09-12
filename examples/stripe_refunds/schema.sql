CREATE SCHEMA IF NOT EXISTS stripe_refund_app;
CREATE TABLE IF NOT EXISTS stripe_refund_app.orders (
    tenant_reference text NOT NULL,
    order_reference text NOT NULL,
    data jsonb NOT NULL,
    PRIMARY KEY (tenant_reference, order_reference)
);
CREATE TABLE IF NOT EXISTS stripe_refund_app.intents (
    tenant_reference text NOT NULL,
    effect_reference text NOT NULL,
    order_reference text NOT NULL,
    data jsonb NOT NULL,
    phase text NOT NULL DEFAULT 'ready' CHECK (phase IN ('ready', 'submitted', 'settled')),
    last_observation jsonb,
    case_open boolean NOT NULL DEFAULT false,
    next_check_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    monitoring_until timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP + interval '31 days',
    PRIMARY KEY (tenant_reference, effect_reference),
    FOREIGN KEY (tenant_reference, order_reference)
        REFERENCES stripe_refund_app.orders (tenant_reference, order_reference)
);
CREATE TABLE IF NOT EXISTS stripe_refund_app.keys (
    handle text PRIMARY KEY,
    tenant_reference text NOT NULL,
    proposal_reference text NOT NULL,
    purpose text NOT NULL,
    wrapped bytea NOT NULL
);
CREATE TABLE IF NOT EXISTS stripe_refund_app.webhooks (
    event_reference text PRIMARY KEY,
    received_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS stripe_intent_order ON stripe_refund_app.intents
    (tenant_reference, order_reference);
CREATE INDEX IF NOT EXISTS stripe_intent_monitoring ON stripe_refund_app.intents
    (tenant_reference, next_check_at) WHERE phase IN ('submitted', 'settled');
CREATE INDEX IF NOT EXISTS stripe_intent_cases ON stripe_refund_app.intents
    (tenant_reference, effect_reference) WHERE case_open;
CREATE TABLE IF NOT EXISTS stripe_refund_app.work_schedule (
    tenant_reference text NOT NULL,
    proposal_reference text NOT NULL,
    lease_token text NOT NULL,
    leased_until timestamptz NOT NULL,
    next_attempt_at timestamptz NOT NULL,
    attention_reason text,
    PRIMARY KEY (tenant_reference, proposal_reference)
);
CREATE OR REPLACE FUNCTION stripe_refund_app.guard_reserved_order()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM stripe_refund_app.intents
        WHERE tenant_reference=OLD.tenant_reference AND order_reference=OLD.order_reference
          AND phase='submitted'
    ) THEN
        RAISE EXCEPTION 'order has an unresolved refund reservation' USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;
CREATE OR REPLACE TRIGGER stripe_reserved_order
BEFORE UPDATE OR DELETE ON stripe_refund_app.orders
FOR EACH ROW EXECUTE FUNCTION stripe_refund_app.guard_reserved_order();
