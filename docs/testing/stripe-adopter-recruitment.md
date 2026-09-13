# Stripe outside-adopter qualification brief

This brief is ready to send to a prospective participant. No outreach has been
sent and no participant has been named as of 13 September 2026.

## Suitable participant

The first participant should own a Python application that already performs
Stripe refunds through a real repository and ordinary payment-writer paths. It
needs an isolated PostgreSQL environment with independent connections and, for
provider qualification, a disposable Stripe test account. The team must be
able to name owners for authorization, database transactions, recovery,
evidence retention and fixture cleanup.

Good candidates are merchant back-office products, PSP/payment integrators,
finance-automation systems, and internal merchant operations teams. A demo
that replaces the adopter's repository with the library's reference ledger
does not qualify.

## Named prospect shortlist

These are public technical prospects, not adopters. None has been contacted or
consented to an exercise.

1. **[python-getpaid-stripe](https://github.com/django-getpaid/python-getpaid-stripe)**
   is the strongest first fit. Its Python processor creates refunds, maps
   status-driven refund events, and ships a simulator for pending,
   `requires_action`, success and late-failure paths. That gives the exercise a
   real adapter seam and a deterministic first conversation before any Stripe
   credentials are involved.
2. **[pretix](https://github.com/pretix/pretix)** is a mature Python/Django
   merchant application with an in-tree Stripe provider and refund paths. It is
   the stronger application-level test of repository and ordinary-writer
   integration, but its breadth makes the first exercise more expensive.
3. **[dj-stripe](https://github.com/dj-stripe/dj-stripe)** models Stripe refund
   state and exposes a Django refund operation. It is a useful ecosystem
   reviewer or adapter partner, although it is a library rather than the final
   merchant host and cannot alone satisfy outside-application qualification.

Approach `python-getpaid-stripe` first, then pretix if the initial fit check
shows that the generic payment processor lacks enough host-owned business state
to exercise the full contract. Invite dj-stripe to review the Django boundary
after one application mapping exists.

## What participation involves

1. A 45-minute architecture mapping: refund intent, tenant/account binding,
   normal writers, authorization and authoritative Stripe query.
2. A credential-free repository exercise against the adopter's isolated
   database using the library-orchestrated twelve-scenario host suite.
3. A sandbox lifecycle exercise for one partial refund, including a lost
   acknowledgement, restart, pending observation and evidence export.
4. A second action using subscription cancellation or a supported credit-note
   disposition after the refund path passes.
5. A short review of integration friction, unclear outcomes and operator
   instructions. Maintainer assistance and failed attempts remain in the
   qualification record.

The participant does not share credentials, DSNs, raw customer records or
production access. Public identity is optional; a durable pseudonym can be
used. The result qualifies one recorded implementation and is not a
certification or universal production claim.

## What the participant receives

- a reviewed mapping of its repository to the reservation contract;
- executable race, expiry, drift, uncertainty and tenant-isolation checks;
- a recorded list of adapter changes and unresolved responsibilities;
- a recovery runbook for the exercised action; and
- a minimized evidence bundle from the sandbox path.

## Outreach draft

> We maintain `threvo-actions`, a typed Python lifecycle for consequential
> operations such as Stripe refunds. We are looking for one application team
> willing to test the integration against its own isolated repository and
> ordinary payment-writer paths. The exercise uses no production credentials or
> customer data. It tests concurrent reservation, state drift, authorization,
> lost acknowledgements, recovery and evidence export. We will help map the
> adapter and record all assistance and failures. The result is evidence about
> this one integration, not a certification. Would your payments or platform
> owner be open to a 45-minute fit check?

For `python-getpaid-stripe`, add: “Your status-driven refund mapping and local
Stripe simulator appear to cover the exact pending and late-failure cases we
need. We would start against the simulator and your normal payment processor,
then decide together whether an application-owned repository exercise is
possible.”

## Intake record

Before scheduling, record:

- participant or durable pseudonym and accountable host owner;
- application/repository revision and Python/PostgreSQL versions;
- refund reader, repository and normal-writer owners;
- Stripe SDK/API versions and test-account cleanup owner;
- authenticated identities and approval channel;
- recovery scheduler/operator and evidence-retention owner; and
- permission boundaries for any sandbox or bounded observation.

If the fit check succeeds, follow the
[outside-host adoption protocol](stripe-adoption-protocol.md) and create the
record from the [adoption entry template](templates/stripe-adoption-entry.md).
