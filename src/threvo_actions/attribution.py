"""Exact attribution for receipts produced by the action runtime."""

from __future__ import annotations

import hashlib
import json
import re
from functools import cache
from importlib import metadata
from pathlib import Path

_DISTRIBUTION_NAME = "threvo-actions"
_UNRELEASED_VERSIONS = frozenset({"0.0.0", "0+unknown"})
_RELEASE_REVISION = re.compile(r"^threvo-actions/(?!0\.0\.0$)[0-9A-Za-z][0-9A-Za-z.!+_-]*$")
_SOURCE_REVISION = re.compile(r"^threvo-actions/commit:[0-9a-f]{40}(?:\+tree:[0-9a-f]{64})?$")


class RuntimeAttributionError(RuntimeError):
    """The runtime cannot identify the exact library code producing receipts."""


def resolve_runtime_revision(*, package_root: Path | None = None) -> str:
    """Return an exact released version or source commit plus package-tree digest."""

    if package_root is None:
        return _resolve_default_runtime_revision()
    return _resolve_runtime_revision_at(package_root)


@cache
def _resolve_default_runtime_revision() -> str:
    return _resolve_runtime_revision_at(Path(__file__).resolve().parent)


def _resolve_runtime_revision_at(package_root: Path) -> str:
    version = _installed_distribution_version()
    if (
        version is not None
        and version not in _UNRELEASED_VERSIONS
        and not _installed_distribution_is_editable()
    ):
        return validate_runtime_revision(f"{_DISTRIBUTION_NAME}/{version}")

    commit = _source_commit(package_root)
    if commit is None:
        raise RuntimeAttributionError(
            "cannot determine an exact runtime revision; install a released distribution "
            "or pass ActionRuntime(runtime_revision=...) with an exact release or commit"
        )
    tree_digest = _package_tree_digest(package_root)
    return validate_runtime_revision(f"{_DISTRIBUTION_NAME}/commit:{commit}+tree:{tree_digest}")


def validate_runtime_revision(value: str) -> str:
    """Reject placeholders and ambiguous identifiers before they enter receipts."""

    if _RELEASE_REVISION.fullmatch(value) is None and _SOURCE_REVISION.fullmatch(value) is None:
        raise RuntimeAttributionError(
            "runtime revision must identify an exact threvo-actions release or 40-character commit"
        )
    return value


def _installed_distribution_version() -> str | None:
    try:
        return metadata.version(_DISTRIBUTION_NAME)
    except metadata.PackageNotFoundError:
        return None


def _installed_distribution_is_editable() -> bool:
    try:
        direct_url = metadata.distribution(_DISTRIBUTION_NAME).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return False
    if direct_url is None:
        return False
    try:
        document = json.loads(direct_url)
    except json.JSONDecodeError:
        return False
    directory = document.get("dir_info")
    return isinstance(directory, dict) and directory.get("editable") is True


def _source_commit(package_root: Path) -> str | None:
    git_dir = _find_git_directory(package_root)
    if git_dir is None:
        return None
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if head.startswith("ref: "):
        commit = _read_git_reference(git_dir, head.removeprefix("ref: "))
    else:
        commit = head
    normalized = commit.lower() if commit is not None else ""
    return normalized if re.fullmatch(r"[0-9a-f]{40}", normalized) is not None else None


def _find_git_directory(package_root: Path) -> Path | None:
    for candidate in (package_root, *package_root.parents):
        marker = candidate / ".git"
        if marker.is_dir():
            return marker
        if not marker.is_file():
            continue
        try:
            content = marker.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not content.startswith("gitdir: "):
            return None
        path = Path(content.removeprefix("gitdir: "))
        return path if path.is_absolute() else (candidate / path).resolve()
    return None


def _read_git_reference(git_dir: Path, reference: str) -> str | None:
    search_roots = [git_dir]
    try:
        common = (git_dir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        common = ""
    if common:
        common_dir = Path(common)
        search_roots.append(
            common_dir if common_dir.is_absolute() else (git_dir / common_dir).resolve()
        )
    for root in search_roots:
        try:
            return (root / reference).read_text(encoding="utf-8").strip()
        except OSError:
            pass
        try:
            packed_refs = (root / "packed-refs").read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        suffix = f" {reference}"
        for line in packed_refs:
            if line.endswith(suffix):
                return line.split(" ", maxsplit=1)[0]
    return None


def _package_tree_digest(package_root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        path
        for path in package_root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    )
    for path in files:
        relative = path.relative_to(package_root).as_posix().encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()
