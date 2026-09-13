# Stripe outside-host adoption status

Status recorded 13 September 2026: **maintainer test bed selected; outside
adoption remains unexercised**.

The repository owner selected the Threvo application as the first realistic
test bed. No prospective open-source project will be contacted. Threvo can
exercise the integration against an existing Python application, Stripe billing
writers, durable PostgreSQL repositories, webhook reconciliation and operator
workflows. Because the same team owns the library and application, the result
will remain maintainer evidence rather than independent adoption.

Version 0.4.1 corrects the qualification mechanism: the library now creates the
fixtures, invokes primitive adapter operations, schedules the races, and judges
all twelve outcomes. The reference PostgreSQL adapter runs that exercise against
the real `PostgresStripeLedger` and an ordinary writer path in CI. This is strong
maintainer regression evidence. It is not outside adoption because the adapter,
database, application writer, and execution are still maintainer-controlled.

The Threvo exercise still needs an application owner to provide and authorize
use of:

- its actual refund repository and ordinary payment writer paths;
- independent database connections in an isolated environment;
- authenticated preparation, decision, execution, read, and operator checks;
- an isolated Stripe sandbox account when provider evidence is included; and
- accountable recovery, custody, retention, and cleanup owners.

When that work begins, follow the
[host protocol](stripe-adoption-protocol.md) and create a new record from the
[evidence template](templates/stripe-adoption-entry.md). Label the record as
maintainer-controlled and preserve failed, assisted, and unexercised scenarios.
The billing repositories and runners are implemented; the application should
repeat a second-action exercise through shared runtime, worker, conflict, and
evidence services.

The [Threvo test-bed brief](threvo-stripe-test-bed.md) records the selected
scope, evidence boundary, intake fields and start conditions. It contains no
outreach task.

Until both exercises are recorded, the library can describe its implemented
recipes and deterministic evidence. It cannot claim external qualification,
repeat adoption, stable promotion, or production validation.
