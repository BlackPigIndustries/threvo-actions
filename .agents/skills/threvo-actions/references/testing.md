# Testing and conformance reference

Read this when adding action tests, custom stores or protection providers, or
failure-recovery coverage.

## Deterministic unit infrastructure

```python
from datetime import UTC, datetime

from threvo_actions import ActionRuntime, MemoryActionStore
from threvo_actions.testing import (
    EphemeralProtection,
    FixedClock,
    RecordingEventSink,
    SequentialIdentifiers,
)

store = MemoryActionStore()
clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
events = RecordingEventSink()
protection = EphemeralProtection(acknowledge_data_loss=True)
runtime = ActionRuntime(
    store=store,
    retention_store=store,  # Test-only alias; production requires a separate role.
    clock=clock,
    identifiers=SequentialIdentifiers(),
    event_sink=events,
    runtime_revision="threvo-actions/commit:0123456789abcdef0123456789abcdef01234567",
)
```

These helpers are process-local test infrastructure. In particular,
`EphemeralProtection` is not encryption and intentionally loses protected data
on restart. The fixed source commit above is test attribution; production
runtimes resolve the installed release or source revision automatically.

## Minimum action matrix

- Happy path: prepare, bind authority, admit once, reconcile, verify, and read.
- Access: deny each operation independently and revoke execution after approval.
- Evidence: reject wrong tenant, action, proposal, effect, commitment, audience,
  channel, authenticated authority, issue time, and expiry.
- Drift: change material state and race resolution against the atomic
  precondition; both paths must produce no effect.
- Concurrency: race proposals with one semantic effect and replay each operation.
- Recovery: crash after admission, respect the recovery lease, and reconcile an
  unknown result without blind resend.
- Verification: exercise provisional and final absence, target unavailability,
  terminal failure, and exhaustion. Executor output alone is never verified.
- Itemized effects: partial outcomes are real and cannot be returned by a
  single-effect definition.
- Leakage and retention: seed sensitive values, scan every generic surface, and
  prove erasure is authorized, resumable, and idempotent.

## Reusable conformance checks

Use the checks that match the extension point:

```python
from threvo_actions.conformance import (
    assert_action_store_conforms,
    assert_no_sensitive_data,
    assert_providers_conform,
    assert_runtime_conforms,
)
```

- `assert_action_store_conforms` for custom stores and tenant isolation;
- `assert_providers_conform` for commitment/protection destruction behavior;
- `assert_runtime_conforms` for a host action's lifecycle driver; and
- `assert_no_sensitive_data` for seeded-canary leakage checks.

Run focused host tests first, then supported Python-version, strict type-check,
lint, security, artifact-build, and clean-wheel smoke gates. Custom adapters
still need domain-specific tests; generic conformance is only a baseline.

Store authors must also test two physical connections racing one effect, stale
revision refusal, transaction rollback, tenant isolation, migration upgrades,
stored-data corruption, and exact acceptance of current lifecycle states with
rejection of retired or unknown states. The SQLite conformance example is the
safe copy/paste fixture; do not teach an in-memory dictionary as a durable
custom-store implementation.

The official MySQL suite additionally runs against native MySQL 8.0 and 8.4,
uses independent pools for effect races, exhausts the lifecycle transition
matrix, checks immutable migration history/schema parity, and proves separated
runtime and retention grants. It must also attack the security-definer routines
with the runtime credential: try protected-snapshot changes, evidence/receipt
rewrites and binding mismatches, direct effect-claim inserts, and cross-effect
claim transfers. Test same-proposal compare-and-set and runtime-versus-retention
races through separate pools. Do not substitute SQLite or a mocked cursor when
changing MySQL transaction, trigger, routine, or migration behavior.
