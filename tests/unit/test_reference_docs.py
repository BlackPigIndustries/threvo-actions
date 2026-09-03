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


def test_readiness_reference_distinguishes_inspection_from_startup_gating() -> None:
    guide = (ROOT / "docs" / "reference" / "readiness.md").read_text()

    assert "may report applied and pending" in guide
    assert "do not validate the application's credential boundary" in guide
    assert "readiness checks uniquely combine" in guide


def test_leakage_example_uses_the_public_safe_argument_contract() -> None:
    guide = (ROOT / "docs" / "testing" / "conformance.md").read_text()

    assert "assert_no_sensitive_data(\n    value=" in guide
    assert "forbidden_literals={" in guide
    assert "corpus=" not in guide
    assert "forbidden_values=" not in guide


def test_quickstarts_distinguish_copy_paste_from_the_source_tour() -> None:
    quickstart = (ROOT / "examples/docs/quickstart.py").read_text()
    installed_quickstart = (ROOT / "examples/docs/installed_quickstart.py").read_text()
    guide = (ROOT / "docs/getting-started/first-action.md").read_text()
    release = (ROOT / ".github/workflows/release.yml").read_text()

    assert sum(bool(line.strip()) for line in quickstart.splitlines()) < 100
    assert "demo.clock.advance(demo.specification.verification_delay)" in quickstart
    assert "from examples." not in installed_quickstart
    assert "import examples." not in installed_quickstart
    assert "installed_quickstart.py" in release
    assert 'mkdir "$consumer_root/wheel-quickstart"' in release
    assert "source distribution" in guide
    assert "production-shaped" in guide
    assert "Copy the file below" in guide

    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "uv run python examples/docs/installed_quickstart.py" not in ci
    assert "name: Verify installed-wheel quickstart" in ci
    assert "uv pip install \\" in ci
    assert '--python "$quickstart_root/venv/bin/python"' in ci
    assert "wheel=$(find dist -maxdepth 1 -name '*.whl' -print -quit)" in ci
    assert '            "$wheel"' in ci
    assert 'cd "$quickstart_root"' in ci


def test_installed_quickstart_minimizes_its_display_preview() -> None:
    quickstart = import_module("examples.docs.installed_quickstart")

    assert set(quickstart.CategorizePreview.model_fields) == {
        "expense_reference",
        "category",
    }
    assert "previous_category" in quickstart.CategorizeSnapshot.model_fields


def test_examples_index_links_to_the_installed_wheel_quickstart() -> None:
    index = (ROOT / "docs" / "examples" / "index.md").read_text()

    assert "[Installed-wheel quickstart](../getting-started/first-action.md)" in index


def test_gradual_reveal_design_uses_the_public_binding_keyword() -> None:
    design = (ROOT / "docs" / "design" / "gradual-reveal-api.md").read_text()

    assert "actions.bind(refund, dependencies=request_deps)" in design
    assert "actions.bind(refund, deps=" not in design
    assert "Agent and worker adapters accept" not in design
    assert "neither a worker adapter nor a scheduler" in design
    assert "| Static type checking | recipe/spec model relationship" in design
    assert "| Registration runtime | duplicate action type and catalog state |" in design


def test_primary_guidance_gates_the_experimental_authoring_surface() -> None:
    readme = (ROOT / "README.md").read_text()
    first_action = (ROOT / "docs" / "getting-started" / "first-action.md").read_text()

    for document in (readme, first_action):
        guide = " ".join(document.split())
        assert "pin" in guide and "exact patch" in guide
        assert "equivalence tests before every patch upgrade" in guide
        assert "migration notes before every minor-line upgrade" in guide
        assert "supported" in guide and "`Action`" in guide
        assert "ActionDefinition" in guide


def test_gradual_reveal_methodology_defines_comparative_loc_scoring() -> None:
    methodology = (ROOT / "docs" / "integration-surface-methodology.md").read_text()

    assert "Definition/composition wiring" in methodology
    assert "Shared first-integration cost" in methodology
    assert "Marginal action cost" in methodology
    assert "absolute_loc_delta = C - E" in methodology
    assert "reduction_percentage = ((E - C) / E) * 100" in methodology
    assert "complete marginal eligible-host LOC" in methodology
    assert "RFC 8785 JCS" in methodology
    assert "encode it as UTF-8" in methodology
    assert "gradual-reveal-adoption.md#integrity-rules" in methodology


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
