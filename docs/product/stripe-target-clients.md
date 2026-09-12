# Target Stripe customers

The first adoption target is a direct merchant with a real PostgreSQL-backed refund workflow, an identifiable finance or operations owner, and ordinary application writers that can be included in concurrency tests. The team must be able to run an isolated Stripe sandbox and implement the library's repository Protocol against its own schema. A wrapper around the bundled example does not qualify as outside adoption.

Use the [outside-host protocol](../testing/stripe-adoption-protocol.md) to collect evidence. Recruit or contact a host only through an explicitly authorized business process; this document does not grant that authority.

AP2 is a possible future purchase-authorization input, not authority for the
current refund and billing actions. See the
[AP2 compatibility decision](../design/ap2-compatibility.md) for the required
trust, host-policy and implementation gates.

## Initial customer

A Python-based SaaS business or commerce platform that uses Stripe and wants
its support or finance assistant to prepare refunds while a separate employee
retains financial authority. It already has an authenticated application,
order/payment records, refund policy and a team responsible for exceptions.

The buyer is usually the engineering lead or platform team responsible for
agent tools, payments and operational reliability. Daily users are support
staff proposing refunds and finance or operations staff approving them.

## Priority segments

| Segment | Concrete trigger | Value to prove | Integration requirements |
| --- | --- | --- | --- |
| SaaS vendors using Stripe directly | A support assistant needs to refund a customer without unrestricted payment access | Exact proposal approval; no duplicate refund after a timeout or restart | Authenticated payment lookup, company refund policy, durable intent store |
| Commerce/support platforms serving multiple merchants | The same assistant workflow acts for several Stripe accounts | Tenant and account separation; consistent recovery across merchants | Host-owned connected-account mapping and direct-charge qualification |
| Internal finance copilots | Staff need controlled tools across payments and accounting | Common authority and evidence semantics, with provider-specific verification | Existing finance roles, business references and operations ownership |
| Agencies/platform integrators building financial agents | Each customer otherwise requires bespoke approval and retry code | Lower engineering effort through a tested adapter and runnable application | Ability to supply host identity, policy, keys and deployment |

Start with direct Stripe merchants. Evaluate direct-charge Connect platforms
second. Destination charges, separate charges/transfers, application-fee
refunds and treasury flows require additional business effects and are outside
the first connector's qualification.

## Qualification questions

1. Does the proposed operation move or return money, rather than merely answer
   a finance question?
2. Is there an authoritative mapping from the signed-in organization and a
   business order to its Stripe account/payment?
3. Who can request, approve and investigate a refund? Must these be different
   people? What policy and limits apply?
4. What happens today after a timeout, duplicate tool call, revoked permission
   or late provider failure?
5. Can the team operate PostgreSQL and a durable worker, and integrate existing
   identity and key custody?
6. Will they implement a second action after the first integration succeeds?

## Cases with limited initial fit

- A merchant that only needs an ordinary checkout or Stripe Dashboard refund.
- A consumer shopping agent needing card provisioning and merchant checkout.
- A high-frequency machine-payment client needing reusable allowances and
  atomic aggregate budgets instead of per-proposal approval.
- A team seeking a certified compliance product, guaranteed exactly-once
  effects, managed payments infrastructure or an investment-analysis agent.

## Adoption offer and evidence

The proposed `StripeActions.refunds` interface packages expert refund behavior
behind explicit typed host contracts. Evaluate whether developers and agents can
understand affected data, handle changed state and revoked authority, and
distinguish submission from completion through the ordinary interface. Ease of
applying strong practices is the primary value; elapsed integration time is
secondary. See the [detailed proposition and pipeline](../plans/2026-09-10-stripe-actions.md).

Offer one assisted integration of the customer's own refund operation, using
their existing policy and systems. Evaluate failure recovery, correct use of
authority and data boundaries, developer confusion and repeat use on a second action. Record
assistance and failed attempts honestly. The included sandbox app and internal
use are engineering evidence, not evidence of independent customer adoption.

The library is Apache-2.0; any paid implementation support or hosted operations
service is a future commercial experiment. There is no proven revenue model
or Stripe partnership implied by this connector.

## Relationship to Visa and Mastercard

For these customers, the initial connection is Stripe's refund API, not a
direct card-network integration. A later corporate-purchasing action could
verify a delegated credential, apply company policy and use a Visa- or
Mastercard-backed payment provider. Mastercard Verifiable Intent/AP2 authority
mapping and Visa Intelligent Commerce remain separate roadmap items. Existing
typed authority evidence does not establish network interoperability.
