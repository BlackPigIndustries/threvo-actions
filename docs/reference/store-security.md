# Store security profiles

Store security profiles make each official adapter's data-handling and
operational boundary inspectable in code. They are configuration claims for
the tested adapter—not legal, regulatory, or third-party certifications.

```python
from threvo_actions.store_security import official_store_security_profiles

for profile in official_store_security_profiles():
    print(profile.identifier, profile.support_tier)
    for claim in profile.guarantee_enforcement:
        print(claim.guarantee, claim.level)
```

## Current profiles

| Profile | Intended topology | Privilege boundary | Qualification targets |
| --- | --- | --- | --- |
| `postgresql/v1` | Multi-process | Separate database roles | PostgreSQL 15 and 16 |
| `mysql/v1` | Multi-process | Separate users with direct grants | MySQL 8.0 and 8.4 |
| `sqlite/v1` | Bounded single writer | Process boundary only | CPython `sqlite3` on Python 3.11-3.13 |

Every profile requires the host to protect private state before persistence.
No official adapter configures storage encryption, authenticates the issuer
named in evidence, or erases WAL/binary logs/journals, replicas, snapshots,
exports, and backups. The profile exposes those facts as false fields so an
application cannot infer them from adapter support.

## Per-guarantee enforcement

Each profile reports where four security-relevant persistence guarantees are
enforced. `database_engine` means database constraints, transactions, triggers,
procedures, or privileges defend the guarantee beneath ordinary adapter code.
`adapter_process` means bypassing that process also bypasses the claim.
`unsupported` means the profile does not provide the guarantee.

| Guarantee | PostgreSQL | MySQL | SQLite |
| --- | --- | --- | --- |
| Lifecycle transitions | Database engine | Database engine | Database engine |
| Atomic effect admission | Database engine | Database engine | Database engine |
| Append-only active evidence | Database engine | Database engine | Adapter process |
| Role-separated erasure | Database engine | Database engine | Unsupported |

These levels describe the qualified adapter configuration, not every account
with administrative access. Database owners and infrastructure operators remain
outside the ordinary runtime/retention boundary.

`independent_connection_conformance=True` means the repository runs the shared
race scenario for that official profile. PostgreSQL and MySQL receive two
separately created pools; SQLite receives two store instances that independently
open the same file. The scenario proves:

- a write through one connection source is visible through the other;
- two stale compare-and-set attempts have exactly one winner;
- two proposals racing one semantic effect produce one acquisition and one
  conflict; and
- both connection sources resolve the same effect owner.

It does not prove network security, credential rotation, backup deletion,
availability, or exactly-once effects outside the action database.

## Qualify a custom store

Run the generic store contract first. Then supply two adapters backed by
separately created connection sources to the independent-connection check:

```python
from threvo_actions.conformance import (
    IndependentStoreConformanceCase,
    assert_independent_store_connections_conform,
)

report = await assert_independent_store_connections_conform(
    IndependentStoreConformanceCase(
        first_store=store_from_first_pool,
        second_store=store_from_second_pool,
        original=fresh_proposal,
        evidence=bound_evidence,
        observed_at=clock.now(),
        security_profile_identifier="acme-document-store/v1",
    )
)
print(report.checks)
```

The helper cannot inspect whether the caller truly created independent
connections. Your test fixture owns that proof and must use a fresh isolated
database. Record the database product/version, isolation level, connection
topology, migration state, and test run outside this deterministic report.

::: threvo_actions.store_security
    options:
      members: true
      show_source: false
