from __future__ import annotations

import os
from pathlib import Path

from mypy import api as mypy_api

CASES = Path(__file__).parent / "cases"


def _run_mypy(case: str, *, cache_dir: Path) -> tuple[str, str, int]:
    previous_mypy_path = os.environ.get("MYPYPATH")
    os.environ["MYPYPATH"] = str(CASES)
    try:
        return mypy_api.run(
            [
                "--strict",
                "--python-version=3.11",
                "--show-error-codes",
                f"--cache-dir={cache_dir}",
                str(CASES / case),
            ]
        )
    finally:
        if previous_mypy_path is None:
            os.environ.pop("MYPYPATH", None)
        else:
            os.environ["MYPYPATH"] = previous_mypy_path


def test_valid_application_preserves_all_four_model_types(tmp_path: Path) -> None:
    stdout, stderr, status = _run_mypy("valid_application.py", cache_dir=tmp_path / "valid")

    assert status == 0, stdout + stderr
    assert "Success: no issues found" in stdout


def test_invalid_application_refuses_mismatched_and_erased_types(tmp_path: Path) -> None:
    stdout, stderr, status = _run_mypy("invalid_application.py", cache_dir=tmp_path / "invalid")

    assert status == 1, stdout + stderr
    assert "Cannot infer value of type parameter" in stdout
    assert 'generic type "RegisteredAction"' in stdout
    assert "[type-arg]" in stdout
