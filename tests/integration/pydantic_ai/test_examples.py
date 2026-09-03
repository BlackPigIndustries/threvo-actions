from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from examples.docs.pydantic_ai_agent import main as run_inline_example
from examples.docs.pydantic_ai_deferred import main as run_deferred_example

if TYPE_CHECKING:
    import pytest


def _assert_verified_output(capsys: pytest.CaptureFixture[str]) -> None:
    assert capsys.readouterr().out.splitlines() == [
        "The refund was submitted for verification.",
        "The refund was authoritatively verified.",
        "executor calls: 1",
    ]


def test_inline_example_reports_completion_after_reconciliation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    asyncio.run(run_inline_example())
    _assert_verified_output(capsys)


def test_deferred_example_reports_completion_after_reconciliation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    asyncio.run(run_deferred_example())
    _assert_verified_output(capsys)
