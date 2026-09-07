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
