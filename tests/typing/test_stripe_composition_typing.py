from __future__ import annotations

import os
from pathlib import Path

from mypy import api as mypy_api

CASES = Path(__file__).parent / "cases"
MYPY_CONFIG = Path(__file__).with_name("mypy.ini")


def _run_mypy(case: str, *, cache_dir: Path) -> tuple[str, str, int]:
    previous = os.environ.get("MYPYPATH")
    os.environ["MYPYPATH"] = str(CASES)
    try:
        return mypy_api.run(
            [
                f"--config-file={MYPY_CONFIG}",
                "--strict",
                "--python-version=3.11",
                "--show-error-codes",
                "--no-color-output",
                f"--cache-dir={cache_dir}",
                str(CASES / case),
            ]
        )
    finally:
        if previous is None:
            os.environ.pop("MYPYPATH", None)
        else:
            os.environ["MYPYPATH"] = previous


def test_valid_stripe_composition_preserves_facade_type(tmp_path: Path) -> None:
    stdout, stderr, status = _run_mypy(
        "valid_stripe_composition.py", cache_dir=tmp_path / "valid"
    )
    assert status == 0, stdout + stderr


def test_invalid_stripe_group_pairing_is_rejected(tmp_path: Path) -> None:
    stdout, stderr, status = _run_mypy(
        "invalid_stripe_composition.py", cache_dir=tmp_path / "invalid"
    )
    assert status == 1, stdout + stderr
    assert 'incompatible type "SubscriptionCancellationConfig"' in stdout
    assert 'expected "RefundConfig | None"' in stdout
