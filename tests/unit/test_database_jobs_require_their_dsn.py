"""A workflow job that provides a test database must fail, not skip, without its DSN.

The same holds for the Stripe sandbox qualification and its secret: each job
that runs ``tests/qualification/stripe`` sets
``THREVO_ACTIONS_REQUIRE_STRIPE_SANDBOX``.

The database suites skip when their DSN variable is unset, so a job that lost or
renamed its DSN would report green while running none of them. Each job that sets
a ``THREVO_ACTIONS_*TEST*_DSN`` variable and runs ``tests/integration`` therefore
also sets ``THREVO_ACTIONS_REQUIRE_TEST_DATABASE``, which turns that skip into a
failure.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_JOB = re.compile(r"^  ([A-Za-z0-9_-]+):[ \t]*(?:#.*)?\n", re.MULTILINE)
_DSN = re.compile(r"^ +THREVO_ACTIONS_\w*TEST\w*_DSN:", re.MULTILINE)
# Job-level env only (six spaces): a flag on one step could miss the step that
# runs the suites. The scan reads text because PyYAML is not a declared dependency.
_REQUIRED = re.compile(
    r"""^      THREVO_ACTIONS_REQUIRE_TEST_DATABASE: *(["']?)1\1 *(?:#.*)?$""", re.MULTILINE
)
_SANDBOX_SUITE = re.compile(r"(?<!--ignore=)tests/qualification/stripe")
_SANDBOX_REQUIRED = re.compile(
    r"""^      THREVO_ACTIONS_REQUIRE_STRIPE_SANDBOX: *(["']?)1\1 *(?:#.*)?$""", re.MULTILINE
)


def _jobs(workflow: str) -> dict[str, str]:
    parts = _JOB.split(workflow.split("\njobs:\n", 1)[1])
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def test_every_job_that_provides_a_test_database_requires_it() -> None:
    database_jobs = {
        f"{path.name}:{name}": bool(_REQUIRED.search(job))
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        for name, job in _jobs(path.read_text()).items()
        if _DSN.search(job) and "tests/integration" in job
    }

    assert {
        "ci.yml:stripe",
        "ci.yml:postgres",
        "ci.yml:mysql",
        "release.yml:qualify-stripe",
    } <= set(database_jobs), "the scan no longer sees the database jobs; fix the scanner"
    missing = [job for job, required in database_jobs.items() if not required]
    assert missing == [], (
        f'add THREVO_ACTIONS_REQUIRE_TEST_DATABASE: "1" to the env of these jobs: {missing}'
    )


def test_every_job_that_runs_the_stripe_sandbox_qualification_requires_it() -> None:
    """The sandbox suite skips without its secret, writing a not-exercised report.

    A qualification job that lost the secret would otherwise report green.
    """
    sandbox_jobs = {
        f"{path.name}:{name}": bool(_SANDBOX_REQUIRED.search(job))
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        for name, job in _jobs(path.read_text()).items()
        if _SANDBOX_SUITE.search(job)
    }

    assert "stripe-qualification.yml:refunds" in sandbox_jobs, "fix the scanner"
    missing = [job for job, required in sandbox_jobs.items() if not required]
    assert missing == [], (
        f'add THREVO_ACTIONS_REQUIRE_STRIPE_SANDBOX: "1" to the env of these jobs: {missing}'
    )


def _run_sandbox_suite(
    *, required: bool, block: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the qualification suite as CI does, without its secrets."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("THREVO_ACTIONS_") and key not in {"GITHUB_SHA"}
    }
    if required:
        env["THREVO_ACTIONS_REQUIRE_STRIPE_SANDBOX"] = "1"
    # A module set to None in sys.modules raises ImportError on import, as a
    # missing optional extra would.
    program = (
        "import sys\n"
        f"if {block!r}: sys.modules[{block!r}] = None\n"
        "import pytest\n"
        "sys.exit(pytest.main(['-q', '-p', 'no:cacheprovider', '-m', 'stripe_sandbox',"
        " 'tests/qualification/stripe']))\n"
    )
    return subprocess.run(  # noqa: S603 — fixed interpreter and static test program.
        [sys.executable, "-c", program],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_without_its_secrets_the_sandbox_suite_skips_unless_required() -> None:
    skipped = _run_sandbox_suite(required=False)
    assert skipped.returncode == 0, skipped.stdout + skipped.stderr
    assert " skipped" in skipped.stdout and " failed" not in skipped.stdout

    required = _run_sandbox_suite(required=True)
    assert required.returncode == 1, required.stdout + required.stderr
    assert "THREVO_ACTIONS_REQUIRE_STRIPE_SANDBOX=1" in required.stdout
    assert " passed" not in required.stdout


@pytest.mark.parametrize("module", ["stripe", "asyncpg"])
def test_a_required_sandbox_run_without_its_extra_never_passes(module: str) -> None:
    # The conftest's module-level importorskip errors as an initial conftest
    # (05/11/2026); a module-level skip in the test files would pass green.
    required = _run_sandbox_suite(required=True, block=module)
    assert required.returncode not in (0, 5), required.stdout + required.stderr
    assert f"could not import '{module}'" in required.stdout + required.stderr
