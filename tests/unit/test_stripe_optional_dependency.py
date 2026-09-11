from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]


def test_custom_stripe_gateways_do_not_require_the_stripe_sdk() -> None:
    program = """
import importlib.abc
import sys

class RejectStripe(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "stripe" or fullname.startswith("stripe."):
            raise ModuleNotFoundError("Stripe SDK deliberately unavailable")
        return None

sys.meta_path.insert(0, RejectStripe())

from threvo_actions.integrations.stripe import (
    StripeActions,
    StripeActionSettings,
    StripeCreditNoteGateway,
    StripeRefundConnector,
    StripeRefundGateway,
    StripeSubscriptionGateway,
    SubscriptionCancellationConfig,
    SubscriptionCancellationHost,
    SubscriptionCancellationPolicy,
)
from threvo_actions import ConfirmingAuthority, GovernedExecutor, MemoryActionStore, SingleApproval
from threvo_actions.testing import EphemeralProtection

assert StripeActions is not None
assert StripeRefundConnector is not None
assert StripeRefundGateway is not None
assert StripeSubscriptionGateway is not None
assert StripeCreditNoteGateway is not None

class FakePort:
    pass

protection = EphemeralProtection(acknowledge_data_loss=True)
actions = StripeActions(
    subscriptions=SubscriptionCancellationConfig(
        host=SubscriptionCancellationHost(repository=FakePort(), authorization=FakePort()),
        policy=SubscriptionCancellationPolicy(),
        settings=StripeActionSettings(
            executor_identity=GovernedExecutor(reference="service:test"),
            authority_audience="service:test",
        ),
        gateway=FakePort(),
    ),
    store=MemoryActionStore(),
    authority_evaluator=SingleApproval(ConfirmingAuthority(reference="user:approver")),
    commitment_provider=protection,
    protection_codec=protection,
)
assert actions.subscriptions is not None
"""

    completed = subprocess.run(  # noqa: S603 — fixed interpreter and static test program.
        [sys.executable, "-c", program],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
