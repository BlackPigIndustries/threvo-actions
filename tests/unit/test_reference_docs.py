from __future__ import annotations

from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_explicit_reference_members_exist_in_the_declared_module() -> None:
    checked: list[str] = []
    for path in sorted((ROOT / "docs" / "reference").glob("*.md")):
        module_name: str | None = None
        reading_members = False
        for line in path.read_text().splitlines():
            if line.startswith("::: "):
                module_name = line.removeprefix("::: ").strip()
                reading_members = False
                continue
            if line.strip() == "members:":
                reading_members = True
                continue
            if not reading_members:
                continue
            if not line.startswith("        - "):
                if line.strip():
                    reading_members = False
                continue
            assert module_name is not None, path
            member_name = line.strip().removeprefix("- ")
            module = import_module(module_name)
            assert hasattr(module, member_name), f"{path}: {module_name}.{member_name}"
            checked.append(f"{module_name}.{member_name}")

    assert checked


def test_leakage_example_uses_the_public_safe_argument_contract() -> None:
    guide = (ROOT / "docs" / "testing" / "conformance.md").read_text()

    assert "assert_no_sensitive_data(\n    value=" in guide
    assert "forbidden_literals={" in guide
    assert "corpus=" not in guide
    assert "forbidden_values=" not in guide


def test_quickstart_is_small_and_described_as_source_distribution_code() -> None:
    quickstart = (ROOT / "examples/docs/quickstart.py").read_text()
    guide = (ROOT / "docs/getting-started/first-action.md").read_text()

    assert sum(bool(line.strip()) for line in quickstart.splitlines()) < 100
    assert "source distribution" in guide
    assert "production-shaped" in guide
    assert "Copy the file below" not in guide


def test_gradual_reveal_design_uses_the_public_binding_keyword() -> None:
    design = (ROOT / "docs" / "design" / "gradual-reveal-api.md").read_text()

    assert "actions.bind(refund, dependencies=request_deps)" in design
    assert "actions.bind(refund, deps=" not in design
    assert "Agent and worker adapters accept" not in design
    assert "neither a worker adapter nor a scheduler" in design
    assert "| Static type checking | recipe/spec model relationship" in design
    assert "| Registration runtime | duplicate action type and catalog state |" in design


def test_gradual_reveal_adoption_worksheet_keeps_all_attempts() -> None:
    worksheet = (ROOT / "docs/testing/gradual-reveal-adoption.md").read_text()

    for requirement in (
        "Expert baseline",
        "Candidate attempts",
        "all nine",
        "source commit",
        "wheel SHA-256",
        "previous entry digest",
        "independent participant",
        "RFC 8785",
        "UTF-8",
        "excludes `entry_digest`",
        "candidate run ID",
        "Workflow-built wheel",
        "workflow-built source-distribution",
        "wall-clock, validated paused, and scored elapsed minutes",
        "absolute gates use scored elapsed time",
        "Support review entries",
        "Production consumer",
        "Post-adoption window",
        "no unresolved",
        "safety anomaly",
        "Stable promotion entries",
        "Qualifying adoption",
        "Independent DX proof",
        "Post-adoption safety proof",
    ):
        assert requirement in worksheet

    versioning = (ROOT / "docs" / "versioning.md").read_text()
    assert "gradual-reveal-adoption.md#support-review-entries" in versioning
    assert "gradual-reveal-adoption.md#stable-promotion-entries" in versioning
