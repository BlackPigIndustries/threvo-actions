CREATE TABLE IF NOT EXISTS stripe_host_app.approval_requests (
    request_reference text PRIMARY KEY,
    tenant_reference text NOT NULL,
    proposal_reference text NOT NULL,
    binding_data jsonb NOT NULL CHECK (jsonb_typeof(binding_data) = 'object'),
    decision_data jsonb CHECK (decision_data IS NULL OR jsonb_typeof(decision_data) = 'object'),
    UNIQUE (tenant_reference, proposal_reference)
);
