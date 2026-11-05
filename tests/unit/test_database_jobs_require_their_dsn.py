"""A workflow job that provides a test database must fail, not skip, without its DSN.

The database suites skip when their DSN variable is unset, so a job that lost or
renamed its DSN would report green while running none of them. Each job that sets
a ``THREVO_ACTIONS_*TEST*_DSN`` variable and runs ``tests/integration`` therefore
also sets ``THREVO_ACTIONS_REQUIRE_TEST_DATABASE``, which turns that skip into a
failure.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_JOB = re.compile(r"^  ([A-Za-z0-9_-]+):[ \t]*(?:#.*)?\n", re.MULTILINE)
_DSN = re.compile(r"^ +THREVO_ACTIONS_\w*TEST\w*_DSN:", re.MULTILINE)
# Job-level env only (six spaces): a flag on one step could miss the step that
# runs the suites. The scan reads text because PyYAML is not a declared dependency.
_REQUIRED = re.compile(
    r"""^      THREVO_ACTIONS_REQUIRE_TEST_DATABASE: *(["']?)1\1 *(?:#.*)?$""", re.MULTILINE
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
