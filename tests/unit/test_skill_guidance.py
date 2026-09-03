from __future__ import annotations

import re
from importlib import import_module
from pathlib import Path

import threvo_actions

SKILL_ROOT = Path(__file__).parents[2] / ".agents" / "skills" / "threvo-actions"


def test_skill_relative_links_resolve() -> None:
    markdown_link = re.compile(r"\[[^]]+\]\(([^)]+)\)")

    skill = SKILL_ROOT / "SKILL.md"
    for target in markdown_link.findall(skill.read_text()):
        if "://" in target or target.startswith("#"):
            continue
        assert (skill.parent / target.split("#", 1)[0]).is_file(), (
            f"SKILL.md links to missing {target}"
        )


def test_skill_version_matches_package() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text()
    assert f'version: "{threvo_actions.__version__}"' in skill


def test_pydantic_ai_skill_uses_the_published_versioned_extra() -> None:
    reference = (SKILL_ROOT / "references" / "pydantic-ai.md").read_text()

    assert (
        f'python -m pip install "threvo-actions[pydantic-ai]=={threvo_actions.__version__}"'
        in reference
    )
    assert "uv sync --extra pydantic-ai --locked" in reference
    assert "git+https://" not in reference
    assert "THREVO_ACTIONS_REF" not in reference


def test_documented_conformance_helpers_are_importable() -> None:
    conformance = import_module("threvo_actions.conformance")

    for name in (
        "assert_action_store_conforms",
        "assert_independent_store_connections_conform",
        "assert_providers_conform",
        "assert_runtime_conforms",
        "assert_no_sensitive_data",
    ):
        assert callable(getattr(conformance, name))


def test_skill_prefers_the_namespaced_gradual_reveal_surface() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text()

    assert "ActionApplication" in skill
    assert "ActionSpec" in skill
    assert "ScopedActionToolBinding" in skill
    assert "Prefer `Action[" not in skill
