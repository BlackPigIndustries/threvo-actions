from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pytest
from scripts import verify_index_artifacts as verifier

if TYPE_CHECKING:
    from pathlib import Path


def test_parse_manifest_rejects_unsafe_or_duplicate_filenames(tmp_path: Path) -> None:
    digest = "a" * 64
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(f"{digest}  ../artifact.whl\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe manifest filename"):
        verifier.parse_manifest(manifest)

    manifest.write_text(
        f"{digest}  artifact.whl\n{digest}  artifact.whl\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate manifest filename"):
        verifier.parse_manifest(manifest)


def test_parse_published_artifacts_rejects_unapproved_download_host() -> None:
    payload = {
        "urls": [
            {
                "filename": "threvo_actions-0.1.1-py3-none-any.whl",
                "url": "https://example.com/artifact.whl",
                "digests": {"sha256": "a" * 64},
            }
        ]
    }

    with pytest.raises(ValueError, match="approved HTTPS host"):
        verifier.parse_published_artifacts(payload)


def test_parse_published_artifacts_keeps_repository_hosts_separate() -> None:
    payload = {
        "urls": [
            {
                "filename": "threvo_actions-0.1.4-py3-none-any.whl",
                "url": "https://files.pythonhosted.org/packages/artifact.whl",
                "digests": {"sha256": "a" * 64},
            }
        ]
    }

    with pytest.raises(ValueError, match="approved HTTPS host"):
        verifier.parse_published_artifacts(
            payload,
            allowed_file_hosts=verifier.INDEX_FILE_HOSTS["test.pypi.org"],
        )


def test_verify_index_artifacts_matches_metadata_and_raw_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = tmp_path / "release"
    packages = release / "packages"
    packages.mkdir(parents=True)
    artifacts = {
        "threvo_actions-0.1.1-py3-none-any.whl": b"wheel",
        "threvo_actions-0.1.1.tar.gz": b"sdist",
    }
    manifest_lines = []
    urls = []
    downloads: dict[str, bytes] = {}
    for filename, content in artifacts.items():
        (packages / filename).write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        manifest_lines.append(f"{digest}  {filename}")
        url = f"https://test-files.pythonhosted.org/packages/{filename}"
        urls.append({"filename": filename, "url": url, "digests": {"sha256": digest}})
        downloads[url] = content
    (release / "SHA256SUMS").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    json_url = "https://test.pypi.org/pypi/threvo-actions/0.1.1/json"
    downloads[json_url] = json.dumps({"info": {"version": "0.1.1"}, "urls": urls}).encode()
    monkeypatch.setattr(verifier, "_download", downloads.__getitem__)

    verified = verifier.verify_index_artifacts(
        version="0.1.1",
        release=release,
        json_url=json_url,
    )

    assert set(verified) == set(artifacts)


def test_verify_index_artifacts_rejects_raw_download_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = tmp_path / "release"
    packages = release / "packages"
    packages.mkdir(parents=True)
    filename = "threvo_actions-0.1.1-py3-none-any.whl"
    content = b"wheel"
    digest = hashlib.sha256(content).hexdigest()
    (packages / filename).write_bytes(content)
    (release / "SHA256SUMS").write_text(f"{digest}  {filename}\n", encoding="utf-8")
    artifact_url = f"https://test-files.pythonhosted.org/packages/{filename}"
    json_url = "https://test.pypi.org/pypi/threvo-actions/0.1.1/json"
    downloads = {
        json_url: json.dumps(
            {
                "info": {"version": "0.1.1"},
                "urls": [
                    {
                        "filename": filename,
                        "url": artifact_url,
                        "digests": {"sha256": digest},
                    }
                ],
            }
        ).encode(),
        artifact_url: b"tampered",
    }
    monkeypatch.setattr(verifier, "_download", downloads.__getitem__)

    with pytest.raises(ValueError, match="downloaded artifact digest differs"):
        verifier.verify_index_artifacts(version="0.1.1", release=release, json_url=json_url)
