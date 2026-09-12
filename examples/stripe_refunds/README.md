# Stripe refund operations

A runnable sandbox application around the optional Stripe connector. Requesters
or the Pydantic AI assistant prepare exact refunds; a separate finance approver
authorizes them. A durable worker submits and verifies approved operations.

## Run with uv

Requires uv, Python 3.11–3.13, PostgreSQL 15+ and Stripe **test** credentials.
Install uv using [the official instructions](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone --branch develop https://github.com/BlackPigIndustries/threvo-actions.git
cd threvo-actions
uv sync --extra stripe-app --locked
createdb threvo_refunds_sandbox
export STRIPE_APP_DATABASE_URL=postgresql:///threvo_refunds_sandbox
```

Create an ignored `.env.local` containing `STRIPE_SECRET_KEY` from a Stripe
sandbox and `STRIPE_WEBHOOK_SECRET` for this app's webhook endpoint. Use a
dedicated local database; do not point initialization at an existing business
database. The migrator creates the library schema and its role grants; the
reference host tables live in `stripe_refund_app`.

```bash
uv run --env-file .env.local python -m examples.stripe_refunds configure
uv run python -m examples.stripe_refunds init
uv run python -m examples.stripe_refunds sandbox-smoke
uv run python -m examples.stripe_refunds serve
```

Open [the app](http://127.0.0.1:8088). The private
`.env.stripe-refunds.json` configuration contains two generated bearer tokens:
one requester and one independent approver. Use the requester token to propose
a refund, then sign out and use the approver token to review it. Tokens stay in
browser memory and are discarded on sign-out; the app stores no browser token
or chat history. The config file is created with mode `0600` and is excluded
from git. Preserve its master key across restarts.

`sandbox-smoke` creates a new $20 test payment, seeds its order, proposes a $2
refund, records approval through the service, and checks the provider result.
Every invocation creates a new test payment. It rejects live keys and live
orders. It does not exercise a real human login or charge real money.

The worker runs in the web process every five seconds. Public, tenant-scoped
database discovery recovers work after restart. A host-owned lease is acquired
immediately before each operation. Failed attempts retain a sanitized reason
and receive a durable 30-second backoff, so duplicate workers cannot dispatch
the same admitted operation together.
A separate scheduler can also run:

```bash
uv run python -m examples.stripe_refunds sweep
```

## Seed an existing sandbox order

Create a private order JSON file and pass it to the host-side seed command.
Amounts are entered in the browser; the seed fixes account, payment and currency
scope before any agent can act.

```json
{
  "tenant_reference": "tenant:demo",
  "order_reference": "order:support-1042",
  "account": {"reference": "merchant:sandbox", "connected_account": null, "livemode": false},
  "charge_id": "ch_REPLACE_WITH_SANDBOX_CHARGE",
  "currency": "USD",
  "currency_exponent": 2,
  "version": 1
}
```

```bash
uv run python -m examples.stripe_refunds seed /private/path/order.json
```

Choose the actual charge currency and exponent. Set `connected_account` only
for a direct connected-account charge that the configured test key can access.
Use a new business intent reference for a new refund; retries of the same
business operation must retain the original reference and parameters.

## Enable the assistant

Set `model` in the private JSON configuration to a supported Pydantic AI model
identifier, and supply its provider credentials through the process environment.
The `stripe-app` extra includes the OpenAI provider; add another provider through
uv if needed. For example:

```bash
uv add "pydantic-ai-slim[anthropic]>=2.33,<3"
```

The assistant uses typed dependencies and the library's `ActionCapability`.
It pauses with a deferred request after preparing a proposal. There is no
client endpoint that accepts model-generated authority, a tenant override,
provider credentials, or replayed agent history. The regular form works
without a model or model-provider credentials.

Set `agent_recovery_enabled` to `true` only when the assistant should also get
the read-only `refund_recovery` tool. Its only input is a safe proposal
reference. It returns the same authorized recovery view as the HTTP endpoint;
it cannot execute, retry, approve, refresh a case, or close one.

## HTTP operations

Authenticated endpoints use `Authorization: Bearer <configured token>`.

| Endpoint | Behavior |
| --- | --- |
| `GET /api/session` | Current role and sandbox mode |
| `GET /api/proposals` | Tenant-scoped minimized proposals and receipts |
| `POST /api/proposals` | Prepare a strict JSON `RefundCommand`; amounts are decimal strings |
| `POST /api/proposals/{reference}/decision` | Independent approver submits `{"approve": true}` or rejection |
| `GET /api/proposals/{reference}/recovery` | Authorized recovery condition, schedule, and safe next step |
| `POST /api/chat` | Prepare through the assistant; no execution authority |
| `GET /api/cases` | Approver's unresolved/late-failure cases |
| `POST /api/cases/{effect}/refresh` | Read fresh provider evidence; never resend |
| `POST /webhooks/stripe` | Verify raw-body signature and deduplicate lookup hints |

## Architecture and operational boundary

- `models.py`: strict, frozen Pydantic domain/configuration models.
- `action.py`: authenticated host authorization for the maintained refund facade.
- `storage.py`, `schema.sql`: host intent binding, per-order reservation,
  case handling and monitoring. Runtime work discovery uses the public
  `PostgresActionWorkSource`.
- `protection.py`: per-proposal envelope keys persisted in PostgreSQL and
  wrapped by a host-owned master key. This is a reference implementation, not AWS KMS.
- `service.py`: `StripeActions` composition, authenticated decisions and the
  public recovery worker recipe.
- `agent.py`: typed Pydantic AI integration at the edge.
- `web.py`, `index.html`: local browser and HTTP application.

This app binds to localhost and uses explicit static credentials for a runnable
reference. Before production, replace those credentials with the host identity
provider, inject managed key custody and distinct database roles, add the
organization's refund policy, and qualify real deployment/monitoring. Do not
reuse the all-purpose initialization account as a production runtime identity.
The worker logs sanitized failures; hosts need alerting when work remains
unresolved. The sample uses asyncpg's default JSON codec, independently of any
application-specific codec.

Only one unresolved refund per order can reserve submission at a time. A
database guard prevents order updates while that reservation is unresolved;
order writers must preserve this guard and the reservation transaction.
A reserved intent is never sent again, even beyond Stripe's idempotency window.
A crash before the actual HTTP call can therefore require manual investigation;
absence alone does not release the reservation or authorize another refund.
Provider status is monitored for 31 days. Retention, backups, master-key rotation
and notifications remain deployment responsibilities.

See [the connector contract](../../docs/integrations/stripe.md),
[target customers](../../docs/product/stripe-target-clients.md), and
[next steps](../../docs/plans/2026-09-08-next-steps.md).
