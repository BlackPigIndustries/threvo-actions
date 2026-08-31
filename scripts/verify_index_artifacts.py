"""Verify that a package index serves the exact CI-built release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from collections.abc import Mapping

TEST_PYPI_JSON_TEMPLATE = "https://test.pypi.org/pypi/threvo-actions/{version}/json"
ALLOWED_INDEX_HOSTS = frozenset({"test.pypi.org"})
ALLOWED_FILE_HOSTS = frozenset({"test-files.pythonhosted.org"})


@dataclass(frozen=True)
class PublishedArtifact:
    filename: str
    sha256: str
    url: str


def parse_manifest(path: Path) -> dict[str, str]:
    """Parse a sha256sum-compatible manifest and reject ambiguous entries."""
    entries: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        digest, separator, filename = raw_line.partition("  ")
        if separator != "  " or len(digest) != 64 or not _is_hex(digest):
            raise ValueError(f"invalid manifest entry on line {line_number}")
        if not filename or Path(filename).name != filename:
            raise ValueError(f"unsafe manifest filename on line {line_number}")
        if filename in entries:
            raise ValueError(f"duplicate manifest filename: {filename}")
        entries[filename] = digest.lower()
    if not entries:
        raise ValueError("artifact manifest is empty")
    return entries


def parse_published_artifacts(payload: object) -> dict[str, PublishedArtifact]:
    """Extract the immutable artifact identity fields from PyPI JSON."""
    root = _require_mapping(payload, "index response")
    urls = root.get("urls")
    if not isinstance(urls, list):
        raise ValueError("index response has no artifact list")

    artifacts: dict[str, PublishedArtifact] = {}
    for position, raw_artifact in enumerate(urls, 1):
        artifact = _require_mapping(raw_artifact, f"artifact {position}")
        filename = _require_string(artifact.get("filename"), f"artifact {position} filename")
        url = _require_string(artifact.get("url"), f"artifact {position} URL")
        digests = _require_mapping(artifact.get("digests"), f"artifact {position} digests")
        sha256 = _require_string(digests.get("sha256"), f"artifact {position} sha256")
        if Path(filename).name != filename or len(sha256) != 64 or not _is_hex(sha256):
            raise ValueError(f"artifact {position} has an invalid identity")
        if filename in artifacts:
            raise ValueError(f"index returned duplicate artifact: {filename}")
        _require_https_host(url, ALLOWED_FILE_HOSTS, "artifact URL")
        artifacts[filename] = PublishedArtifact(filename, sha256.lower(), url)
    if not artifacts:
        raise ValueError("index returned no artifacts")
    return artifacts


def verify_index_artifacts(*, version: str, release: Path, json_url: str) -> dict[str, str]:
    """Compare local files, index metadata, and downloaded bytes to one manifest."""
    manifest = parse_manifest(release / "SHA256SUMS")
    package_dir = release / "packages"
    local_names = {path.name for path in package_dir.iterdir() if path.is_file()}
    if local_names != set(manifest):
        raise ValueError("local package files differ from the release manifest")
    for filename, expected_digest in manifest.items():
        if _sha256_file(package_dir / filename) != expected_digest:
            raise ValueError(f"local artifact digest differs: {filename}")

    _require_https_host(json_url, ALLOWED_INDEX_HOSTS, "index JSON URL")
    payload: object = json.loads(_download(json_url))
    root = _require_mapping(payload, "index response")
    info = _require_mapping(root.get("info"), "index project metadata")
    if info.get("version") != version:
        raise ValueError("index project version differs from the requested release")
    published = parse_published_artifacts(payload)
    if set(published) != set(manifest):
        raise ValueError("published artifact files differ from the release manifest")

    verified: dict[str, str] = {}
    for filename, expected_digest in manifest.items():
        artifact = published[filename]
        if artifact.sha256 != expected_digest:
            raise ValueError(f"index digest differs: {filename}")
        downloaded_digest = hashlib.sha256(_download(artifact.url)).hexdigest()
        if downloaded_digest != expected_digest:
            raise ValueError(f"downloaded artifact digest differs: {filename}")
        verified[filename] = expected_digest
    return verified


def _download(url: str) -> bytes:
    # Every caller validates the exact HTTPS host before reaching this boundary.
    request = Request(  # noqa: S310
        url, headers={"User-Agent": "threvo-actions-release-verifier/1"}
    )
    with urlopen(  # noqa: S310  # nosec B310
        request, timeout=30
    ) as response:
        content: object = response.read()
    if not isinstance(content, bytes):
        raise ValueError("index response was not bytes")
    return content


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object with string keys")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_https_host(url: str, allowed_hosts: frozenset[str], label: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise ValueError(f"{label} must use an approved HTTPS host")


def _is_hex(value: str) -> bool:
    return all(character in "0123456789abcdefABCDEF" for character in value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--release", type=Path, default=Path("release"))
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--delay", type=float, default=10.0)
    args = parser.parse_args()
    if args.attempts < 1 or args.delay < 0:
        parser.error("attempts must be positive and delay must be non-negative")

    json_url = TEST_PYPI_JSON_TEMPLATE.format(version=args.version)
    last_error: ValueError | HTTPError | URLError | json.JSONDecodeError | None = None
    for attempt in range(1, args.attempts + 1):
        try:
            verified = verify_index_artifacts(
                version=args.version,
                release=args.release,
                json_url=json_url,
            )
        except (ValueError, HTTPError, URLError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt != args.attempts:
                time.sleep(args.delay)
                continue
            print(f"index verification failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"version": args.version, "artifacts": verified}, sort_keys=True))
        return 0
    print(f"index verification failed: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
