from pathlib import Path

SKILL = Path(__file__).parents[2] / ".agents" / "skills" / "threvo-actions" / "SKILL.md"


def test_skill_points_production_custody_to_the_kms_reference() -> None:
    skill = SKILL.read_text()

    assert "AwsKmsEnvelopeProtection" in skill
    assert "host-owned durable wrapped-key store" in skill
    assert "rather than adding an SDK to the core" in skill
