"""Verify release metadata and built distribution contents."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import tarfile
import tomllib
import unicodedata
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

ROOT = Path(__file__).parents[1]
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
FORBIDDEN_PARTS = {
    ".env",
    ".git",
    ".github",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "site",
    "tests",
    "uv.lock",
}
REQUIRED_PACKAGE_FILES = {
    "threvo_actions/.agents/skills/threvo-actions/SKILL.md",
    "threvo_actions/experimental/__init__.py",
    "threvo_actions/experimental/application.py",
    "threvo_actions/experimental/inspection.py",
    "threvo_actions/_migrations/mysql/001_action_runtime.sql",
    "threvo_actions/_migrations/mysql/002_harden_database_boundaries.sql",
    "threvo_actions/_migrations/postgres/001_action_runtime.sql",
    "threvo_actions/_migrations/postgres/002_stale_no_effect.sql",
    "threvo_actions/_migrations/postgres/003_generated_lifecycle_guard.sql",
    "threvo_actions/_migrations/postgres/004_active_lifecycle_guard.sql",
    "threvo_actions/_migrations/sqlite/001_action_runtime.sql",
    "threvo_actions/golden/canonical-v1.json",
    "threvo_actions/golden/receipt-v1.json",
    "threvo_actions/py.typed",
}
REQUIRED_SDIST_FILES = {
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "docs/versioning.md",
    "docs/releases/0.1.4.md",
    "docs/testing/gradual-reveal-adoption.md",
    "examples/docs/quickstart.py",
    "examples/refund/app.py",
    "examples/supplier_destination/app.py",
    "tests/golden/canonical-v1.json",
    "tests/golden/receipt-v1.json",
}
PRIVATE_CONTEXT_FINGERPRINTS: dict[int, frozenset[str]] = {
    11: frozenset({"1ec506a503433097dbcdd0ce7ec1db7f54e7498ed287ce76d0a740805f870a0d"}),
    15: frozenset({"ebe6f06aa9d3aa103669c9086de6fc0d9969b9e3bc6364280f1a5aa7e89e51e3"}),
    17: frozenset({"810623acabb857ba6ba331274b685ef67912d4305cfe48285d2d0deb7f22a094"}),
    18: frozenset({"dcb4d7fb159ba1b1b940cd8b08475d6df06fe4f286c8a4e05043bb1d709df47c"}),
    19: frozenset({"67d22b7a7fd36503bc51a3928451975fc0ffcd735397760833bafcb1c0ccdac3"}),
    23: frozenset({"6f42ae4e83c3ccfc582b721c14ba879517bcedf9d86e2085cba0ca351235446e"}),
    26: frozenset(
        {
            "204a5b4c3384552d80f8259efa269c759e10a0eb21ed916b95d4a615a46d9f9f",
            "4be1ca32517b58e5220874125b1a4c6099239e384d8d60e01fb05dfbd579d6d8",
        }
    ),
    28: frozenset({"410f9c229ce9e16d25860e793fe937ecb19e95e29482a96819ea27842411af9b"}),
    40: frozenset({"7c89f92570717f27a9b316b38639f3fb80e59fe89f00b46dc60fc62bf94ea8a9"}),
    49: frozenset({"6767811c283ea9253d461c4913fc21211d328f0a5cfd30db02435a5a21c010bc"}),
}
PUBLICATION_SCAN_EXCLUDED_DIRS = {
    ".git",
    ".hypothesis",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "site",
}


def project_version() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError("project.version must be an exact three-part release")
    return version


def verify_metadata(*, expected_tag: str | None) -> str:
    version = project_version()
    init = (ROOT / "src/threvo_actions/__init__.py").read_text(encoding="utf-8")
    skill = (ROOT / ".agents/skills/threvo-actions/SKILL.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    _require(f'__version__ = "{version}"' in init, "package __version__ differs")
    _require(f'version: "{version}"' in skill, "bundled skill version differs")
    _require(f"## [{version}] - " in changelog, "changelog has no dated release")
    _assert_no_private_context(_publication_source_contents())
    if expected_tag is not None:
        _require(expected_tag == f"v{version}", "tag does not match package version")
    return version


def verify_distributions(dist: Path) -> None:
    version = project_version()
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    _require(len(wheels) == 1, "release must contain exactly one wheel")
    _require(len(sdists) == 1, "release must contain exactly one source distribution")
    _require(version.replace("-", "_") in wheels[0].name, "wheel version differs")
    _require(version in sdists[0].name, "source distribution version differs")

    with zipfile.ZipFile(wheels[0]) as archive:
        wheel_names = set(archive.namelist())
        _require(wheel_names >= REQUIRED_PACKAGE_FILES, "wheel is missing required files")
        _require(
            any(name.endswith(".dist-info/licenses/LICENSE") for name in wheel_names),
            "wheel is missing license metadata",
        )
        _assert_clean_names(wheel_names, wheel=True)
        _assert_no_workspace_paths(archive.read(name) for name in wheel_names if _is_text(name))
        _assert_no_private_context(archive.read(name) for name in wheel_names if _is_text(name))

    with tarfile.open(sdists[0]) as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        names = {member.name.split("/", 1)[1] for member in members}
        source_package_files = {
            f"src/{name}" for name in REQUIRED_PACKAGE_FILES if "/golden/" not in name
        }
        source_package_files.remove("src/threvo_actions/.agents/skills/threvo-actions/SKILL.md")
        required = (
            source_package_files
            | REQUIRED_SDIST_FILES
            | {
                ".agents/skills/threvo-actions/SKILL.md",
                f"docs/releases/{version}.md",
            }
        )
        _require(required <= names, "source distribution is missing required files")
        _assert_clean_names(names, wheel=False)
        contents = []
        for member in members:
            if not _is_text(member.name):
                continue
            extracted = archive.extractfile(member)
            if extracted is not None:
                contents.append(extracted.read())
        _assert_no_workspace_paths(contents)
        _assert_no_private_context(contents)

    manifest = dist / "SHA256SUMS"
    lines = [f"{_sha256(path)}  {path.name}" for path in (wheels[0], sdists[0])]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def record_candidate(
    release: Path,
    *,
    source_commit: str,
    release_tag: str,
) -> None:
    """Record immutable candidate identity next to already verified packages."""
    _require(COMMIT_PATTERN.fullmatch(source_commit) is not None, "invalid source commit")
    version = project_version()
    _require(release_tag == f"v{version}", "candidate tag does not match package version")
    artifacts = _verified_release_artifacts(release)
    record = {
        "schema_version": 1,
        "version": version,
        "release_tag": release_tag,
        "source_commit": source_commit,
        "artifacts": artifacts,
    }
    (release / "CANDIDATE.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def verify_candidate(
    release: Path,
    *,
    source_commit: str,
    release_tag: str,
) -> None:
    """Refuse promotion when candidate identity or package bytes have changed."""
    record_path = release / "CANDIDATE.json"
    _require(record_path.is_file(), "candidate record is missing")
    record: object = json.loads(record_path.read_text(encoding="utf-8"))
    _require(isinstance(record, dict), "candidate record must be an object")
    expected = {
        "schema_version": 1,
        "version": project_version(),
        "release_tag": release_tag,
        "source_commit": source_commit,
        "artifacts": _verified_release_artifacts(release),
    }
    _require(record == expected, "candidate identity or artifact digests differ")


def _verified_release_artifacts(release: Path) -> dict[str, str]:
    manifest = release / "SHA256SUMS"
    packages = release / "packages"
    _require(manifest.is_file(), "candidate manifest is missing")
    _require(packages.is_dir(), "candidate packages are missing")
    artifacts: dict[str, str] = {}
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        digest, separator, filename = line.partition("  ")
        _require(separator == "  ", f"invalid manifest entry on line {line_number}")
        _require(re.fullmatch(r"[0-9a-f]{64}", digest) is not None, "invalid artifact digest")
        _require(Path(filename).name == filename and filename, "unsafe artifact filename")
        _require(filename not in artifacts, "duplicate artifact filename")
        artifact = packages / filename
        _require(artifact.is_file(), f"candidate artifact is missing: {filename}")
        _require(_sha256(artifact) == digest, f"candidate artifact digest differs: {filename}")
        artifacts[filename] = digest
    wheel_count = sum(filename.endswith(".whl") for filename in artifacts)
    source_distribution_count = sum(filename.endswith(".tar.gz") for filename in artifacts)
    _require(
        len(artifacts) == 2 and wheel_count == 1 and source_distribution_count == 1,
        "candidate must contain one wheel and one source distribution",
    )
    _require(
        {path.name for path in packages.iterdir() if path.is_file()} == set(artifacts),
        "candidate package files differ from the manifest",
    )
    return artifacts


def _assert_clean_names(names: set[str], *, wheel: bool) -> None:
    for name in names:
        parts = set(Path(name).parts)
        forbidden = FORBIDDEN_PARTS - ({"tests"} if not wheel else set())
        _require(not (parts & forbidden), f"forbidden distribution path: {name}")
        _require(not name.endswith((".pyc", ".DS_Store")), f"forbidden file: {name}")
        if not wheel and "tests" in parts:
            _require("golden" in parts, f"only golden vectors may ship from tests: {name}")


def _assert_no_workspace_paths(contents: Iterable[bytes]) -> None:
    needles = (b"/Users/", b"/home/runner/work/", b"Invoice-Ex-Machina")
    for content in contents:
        _require(not any(needle in content for needle in needles), "artifact leaks a local path")


def _assert_no_private_context(
    contents: Iterable[bytes],
    *,
    fingerprints: Mapping[int, Collection[str]] = PRIVATE_CONTEXT_FINGERPRINTS,
) -> None:
    for content in contents:
        normalized = _normalize_for_fingerprinting(content)
        for size, forbidden in fingerprints.items():
            for offset in range(max(len(normalized) - size + 1, 0)):
                fingerprint = hashlib.sha256(normalized[offset : offset + size]).hexdigest()
                _require(
                    fingerprint not in forbidden,
                    "public artifact contains private host-application context",
                )


def _normalize_for_fingerprinting(content: bytes) -> bytes:
    text = content.decode("utf-8", errors="ignore")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = unicodedata.normalize("NFKC", html.unescape(text)).casefold()
    return "".join(character for character in text if character.isalnum()).encode()


def _publication_source_contents() -> Iterable[bytes]:
    return (path.read_bytes() for path in _publication_source_paths())


def _publication_source_paths() -> tuple[Path, ...]:
    paths: list[Path] = []
    for current, directories, filenames in os.walk(ROOT):
        directories[:] = [
            directory
            for directory in directories
            if directory not in PUBLICATION_SCAN_EXCLUDED_DIRS
        ]
        paths.extend(Path(current) / filename for filename in filenames)
    return tuple(sorted(path for path in paths if path.is_file()))


def _is_text(name: str) -> bool:
    return Path(name).suffix in {"", ".json", ".md", ".py", ".sql", ".toml", ".txt"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag")
    parser.add_argument("--dist", type=Path)
    candidate = parser.add_mutually_exclusive_group()
    candidate.add_argument("--record-candidate", type=Path)
    candidate.add_argument("--verify-candidate", type=Path)
    parser.add_argument("--source-commit")
    args = parser.parse_args()
    try:
        version = verify_metadata(expected_tag=args.tag)
        if args.dist is not None:
            verify_distributions(args.dist)
        if (args.record_candidate is not None or args.verify_candidate is not None) and (
            args.source_commit is None or args.tag is None
        ):
            raise ValueError("candidate verification requires --source-commit and --tag")
        if args.record_candidate is not None:
            record_candidate(
                args.record_candidate,
                source_commit=args.source_commit,
                release_tag=args.tag,
            )
        if args.verify_candidate is not None:
            verify_candidate(
                args.verify_candidate,
                source_commit=args.source_commit,
                release_tag=args.tag,
            )
    except ValueError as exc:
        print(f"release verification failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "version": version,
                "distributions": args.dist is not None,
                "candidate_recorded": args.record_candidate is not None,
                "candidate_verified": args.verify_candidate is not None,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
