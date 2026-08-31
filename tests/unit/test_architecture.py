from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import threvo_actions

CORE_ROOT = Path(__file__).parents[2] / "src" / "threvo_actions"
REPOSITORY_ROOT = Path(__file__).parents[2]
ALLOWED_EXTERNAL_IMPORTS = {"pydantic"}
ALLOWED_OPTIONAL_IMPORTS = {Path("cli.py"): {"aiomysql", "asyncpg"}}
HISTORICAL_CLEAN_ROOM_REPORT = Path("docs/testing/clean-room-adoption-2026-08-30.md")


def test_core_has_no_optional_or_host_dependency_imports() -> None:
    unexpected: set[str] = set()

    for path in CORE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.partition(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = {node.module.partition(".")[0]}
            else:
                continue
            allowed = ALLOWED_EXTERNAL_IMPORTS | ALLOWED_OPTIONAL_IMPORTS.get(
                path.relative_to(CORE_ROOT), set()
            )
            if path.relative_to(CORE_ROOT).parts[0] == "integrations":
                allowed = allowed | {"pydantic_ai"}
            unexpected.update(
                root
                for root in roots
                if root not in sys.stdlib_module_names
                and root not in allowed
                and root != "threvo_actions"
            )

    assert unexpected == set()


def test_installation_docs_use_an_exact_release_instead_of_a_moving_source() -> None:
    discovered: list[tuple[Path, str]] = []
    excluded_roots = {".git", ".venv", "dist", "site"}
    for path in REPOSITORY_ROOT.rglob("*.md"):
        relative = path.relative_to(REPOSITORY_ROOT)
        if relative == HISTORICAL_CLEAN_ROOM_REPORT or excluded_roots & set(relative.parts):
            continue
        for version in re.findall(
            r"threvo-actions(?:\[[^]]+\])?==([0-9]+\.[0-9]+\.[0-9]+)", path.read_text()
        ):
            discovered.append((relative, version))

    assert discovered
    assert {version for _, version in discovered} == {threvo_actions.__version__}


def test_custom_store_validation_helpers_are_public() -> None:
    assert callable(threvo_actions.validate_proposal_create)
    assert callable(threvo_actions.validate_proposal_update)


def test_host_extension_contract_is_exported_from_the_top_level_package() -> None:
    expected = {
        "AuthorizationDeniedError",
        "AuthorizationPort",
        "AuthorityEvaluatorPort",
        "Clock",
        "EventSink",
        "GovernedExecutorPort",
        "IdentifierProvider",
        "InvalidActionResultError",
        "NoopEventSink",
        "PreparationPort",
        "RetentionPort",
        "StateResolverPort",
        "VerifierPort",
        "assert_definition_conforms",
    }

    assert expected <= set(threvo_actions.__all__)
    assert all(hasattr(threvo_actions, name) for name in expected)


def test_public_surface_has_no_inert_action_descriptor_vocabulary() -> None:
    removed = {
        "ActionDescriptor",
        "ConfirmFirstControl",
        "Effect",
        "ItemizedEffect",
        "SingleEffect",
    }

    assert removed.isdisjoint(threvo_actions.__all__)
    assert all(not hasattr(threvo_actions, name) for name in removed)
