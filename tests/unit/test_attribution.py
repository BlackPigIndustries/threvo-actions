from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from threvo_actions.attribution import (
    RuntimeAttributionError,
    resolve_runtime_revision,
    validate_runtime_revision,
)


@pytest.fixture(autouse=True)
def clear_default_resolution_cache() -> None:
    from threvo_actions.attribution import _resolve_default_runtime_revision

    _resolve_default_runtime_revision.cache_clear()
    yield
    _resolve_default_runtime_revision.cache_clear()


if TYPE_CHECKING:
    from pathlib import Path


def test_released_distribution_uses_its_exact_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "threvo_actions.attribution._installed_distribution_version",
        lambda: "0.1.2",
    )
    monkeypatch.setattr(
        "threvo_actions.attribution._installed_distribution_is_editable",
        lambda: False,
    )

    assert resolve_runtime_revision() == "threvo-actions/0.1.2"


def test_current_checkout_never_resolves_to_the_unreleased_placeholder() -> None:
    revision = resolve_runtime_revision()

    assert revision.startswith("threvo-actions/commit:")
    assert "+tree:" in revision


def test_editable_release_candidate_uses_source_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package = tmp_path / "src" / "threvo_actions"
    package.mkdir(parents=True)
    (package / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "threvo_actions.attribution._installed_distribution_version",
        lambda: "0.1.0",
    )
    monkeypatch.setattr(
        "threvo_actions.attribution._installed_distribution_is_editable",
        lambda: True,
    )
    monkeypatch.setattr(
        "threvo_actions.attribution._source_commit",
        lambda _package_root: "a" * 40,
    )

    revision = resolve_runtime_revision(package_root=package)

    assert revision.startswith(f"threvo-actions/commit:{'a' * 40}+tree:")


def test_source_checkout_uses_commit_and_package_tree_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package = tmp_path / "src" / "threvo_actions"
    package.mkdir(parents=True)
    (package / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "threvo_actions.attribution._installed_distribution_version",
        lambda: "0.0.0",
    )
    monkeypatch.setattr(
        "threvo_actions.attribution._source_commit",
        lambda _package_root: "a" * 40,
    )

    first = resolve_runtime_revision(package_root=package)
    (package / "runtime.py").write_text("VALUE = 2\n", encoding="utf-8")
    second = resolve_runtime_revision(package_root=package)

    assert first.startswith(f"threvo-actions/commit:{'a' * 40}+tree:")
    assert len(first.rsplit(":", maxsplit=1)[1]) == 64
    assert second != first


def test_unreleased_installed_artifact_without_source_identity_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "threvo_actions.attribution._installed_distribution_version",
        lambda: "0.0.0",
    )
    monkeypatch.setattr(
        "threvo_actions.attribution._source_commit",
        lambda _package_root: None,
    )

    with pytest.raises(RuntimeAttributionError, match="exact runtime revision"):
        resolve_runtime_revision(package_root=tmp_path)


@pytest.mark.parametrize(
    "value",
    [
        "threvo-actions/0.0.0",
        "0.1.0",
        "threvo-actions/commit:short",
        f"threvo-actions/commit:{'a' * 40}+tree:short",
    ],
)
def test_runtime_revision_rejects_placeholder_or_ambiguous_values(value: str) -> None:
    with pytest.raises(RuntimeAttributionError):
        validate_runtime_revision(value)
