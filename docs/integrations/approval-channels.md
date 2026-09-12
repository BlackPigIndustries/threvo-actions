# Authenticated approval channels

The Stripe reference host demonstrates a first-party approval request and
callback boundary without making message delivery part of the action runtime.
The host may put an opaque request reference in email, chat, or its own UI. The
recipient must still authenticate to the host before reading or deciding it.

## Server-owned binding

`POST /api/approval-requests` accepts only a proposal reference and an intended
approver reference from an authenticated requester. Before returning an opaque
request reference, the host persists:

- tenant and proposal instance;
- semantic effect and action type;
- exact proposal commitment;
- intended authenticated authority;
- authority audience and channel assurance;
- creation and expiry times.

The callback accepts only the request reference in the URL and an `approve` or
`reject` decision. It cannot submit tenant, authority, commitment, audience,
assurance, or effect fields. Those fields are loaded from the immutable binding
and compared with the current proposal before the existing
`record_authority(...)` boundary is called.

```json
{"decision": "approve"}
```

The first decision is persisted with its exact `AuthorityEvidence` before it is
forwarded to the runtime. A repeated callback reuses those bytes. Concurrent
matching callbacks converge on the first record; a contradictory callback is
refused. If the HTTP response is lost after authority was recorded, a retry is
an idempotent replay. If it was lost before authority recording, the same
persisted evidence is retried. Current approver rights are evaluated again by
the action authorization port on every runtime call.

## Host responsibilities

The example bearer tokens are sandbox credentials. A production host must bind
the request to its authenticated tenant session, protect the opaque reference in
transit, rate-limit reads and callbacks, and record access in its audit system.
It must also define how approver revocation changes `can_decide`. A delivery
service receives only the opaque request reference and a host URL; it does not
receive protected snapshots, provider IDs, or authority evidence.

Expiry never grants an extension. The host refuses a first decision after the
bound proposal expires. Approval records do not execute Stripe operations;
normal runtime execution and independent verification remain required.

`examples/stripe_host/approvals.py` contains the Pydantic records and PostgreSQL
store. `examples/stripe_host/migrations/approval_requests.sql` is the reusable
host migration, and the refund reference application includes the same table in
its local schema. Third-party webhook signatures and identity mappings require
a channel-specific design; they are outside this first-party recipe.

