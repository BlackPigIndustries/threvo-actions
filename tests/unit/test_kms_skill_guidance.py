from pathlib import Path

SKILL = Path(__file__).parents[2] / ".agents" / "skills" / "threvo-actions" / "SKILL.md"
KMS_REFERENCE = Path(__file__).parents[2] / "docs" / "reference" / "aws-kms.md"


def test_skill_points_production_custody_to_the_kms_reference() -> None:
    skill = SKILL.read_text()

    assert "AwsKmsEnvelopeProtection" in skill
    assert "host-owned durable wrapped-key store" in skill
    assert "rather than adding an SDK to the core" in skill


def test_kms_api_reference_states_its_install_prerequisite() -> None:
    reference = KMS_REFERENCE.read_text()

    assert "This API is unreleased" in reference
    assert "uv sync --extra aws-kms --locked" in reference
    assert "../getting-started/installation.md#optional-integrations" in reference
    assert "`0.1.4` release does not contain this extra" in reference
