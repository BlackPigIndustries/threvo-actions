from __future__ import annotations

import asyncio
import base64
import importlib
import json
import os
import sys
from typing import TYPE_CHECKING

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ValidationError

from threvo_actions.conformance import assert_providers_conform
from threvo_actions.integrations.aws_kms import (
    AwsKmsEnvelopeProtection,
    GeneratedDataKey,
    WrappedDataKey,
)
from threvo_actions.models import ProposalIdentity

if TYPE_CHECKING:
    from collections.abc import Mapping


def _context_bytes(context: Mapping[str, str]) -> bytes:
    return json.dumps(context, sort_keys=True, separators=(",", ":")).encode()


def _flip_last_byte(value: bytes) -> bytes:
    return value[:-1] + bytes((value[-1] ^ 1,))


def _proposal(
    proposal_reference: str,
    *,
    tenant_reference: str = "tenant:test",
) -> ProposalIdentity:
    return ProposalIdentity(
        tenant_reference=tenant_reference,
        proposal_reference=proposal_reference,
    )


def test_import_without_cryptography_extra_has_a_clear_install_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "threvo_actions.integrations.aws_kms"
    monkeypatch.delitem(sys.modules, module_name)
    for loaded_module in tuple(sys.modules):
        if loaded_module.startswith("cryptography."):
            monkeypatch.delitem(sys.modules, loaded_module)
    monkeypatch.setitem(sys.modules, "cryptography", None)

    with pytest.raises(ImportError, match=r"threvo-actions\[aws-kms\]"):
        importlib.import_module(module_name)


class FakeKmsClient:
    def __init__(self, *, plaintext: bytes | None = None) -> None:
        self.alias_target = "arn:aws:kms:eu-west-1:123456789012:key/one"
        master_key = os.urandom(32)
        self._master_keys = {
            self.alias_target: master_key,
            "alias/threvo-actions": master_key,
        }
        self._plaintext = plaintext
        self.contexts: list[dict[str, str]] = []
        self.generated_key_ids: list[str] = []
        self.last_decrypted_plaintext: bytearray | None = None

    def repoint_alias(self) -> None:
        self.alias_target = "arn:aws:kms:eu-west-1:123456789012:key/two"
        self._master_keys[self.alias_target] = os.urandom(32)
        self._master_keys["alias/threvo-actions"] = self._master_keys[self.alias_target]

    async def generate_data_key(
        self,
        *,
        key_id: str,
        encryption_context: Mapping[str, str],
        number_of_bytes: int,
    ) -> GeneratedDataKey:
        assert number_of_bytes == 32
        self.generated_key_ids.append(key_id)
        plaintext = self._plaintext or os.urandom(number_of_bytes)
        nonce = os.urandom(12)
        context = dict(encryption_context)
        self.contexts.append(context)
        ciphertext = nonce + AESGCM(self._master_keys[self.alias_target]).encrypt(
            nonce,
            plaintext,
            _context_bytes(context),
        )
        return GeneratedDataKey(
            plaintext=bytearray(plaintext),
            ciphertext=ciphertext,
            resolved_key_id=self.alias_target,
        )

    async def decrypt_data_key(
        self,
        *,
        key_id: str,
        ciphertext: bytes,
        encryption_context: Mapping[str, str],
    ) -> bytearray:
        nonce, encrypted = ciphertext[:12], ciphertext[12:]
        self.last_decrypted_plaintext = bytearray(
            AESGCM(self._master_keys[key_id]).decrypt(
                nonce,
                encrypted,
                _context_bytes(encryption_context),
            )
        )
        return self.last_decrypted_plaintext


class MemoryEnvelopeStore:
    def __init__(self) -> None:
        self.entries: dict[str, WrappedDataKey] = {}

    async def put(self, *, key_handle: str, envelope: WrappedDataKey) -> None:
        if key_handle in self.entries:
            raise ValueError("duplicate key handle")
        self.entries[key_handle] = envelope

    async def get(self, *, key_handle: str) -> WrappedDataKey | None:
        return self.entries.get(key_handle)

    async def delete(self, *, key_handle: str) -> None:
        self.entries.pop(key_handle, None)


def test_kms_envelope_protection_satisfies_the_provider_contract() -> None:
    async def scenario() -> None:
        kms = FakeKmsClient()
        envelopes = MemoryEnvelopeStore()
        protection = AwsKmsEnvelopeProtection(
            key_id="arn:aws:kms:eu-west-1:123456789012:key/example",
            kms=kms,
            envelopes=envelopes,
        )

        await assert_providers_conform(
            commitment_provider=protection,
            protection_codec=protection,
            proposal_reference="proposal:1",
            canonical_payload=b'{"account":"private"}',
            mutated_payload=b'{"account":"changed"}',
        )

        assert not envelopes.entries
        assert {context["threvo-actions:purpose"] for context in kms.contexts} == {
            "commitment",
            "payload",
        }
        assert {context["threvo-actions:proposal"] for context in kms.contexts} == {"proposal:1"}

    asyncio.run(scenario())


def test_payload_metadata_and_ciphertext_are_authenticated() -> None:
    async def scenario() -> None:
        protection = AwsKmsEnvelopeProtection(
            key_id="arn:aws:kms:eu-west-1:123456789012:key/example",
            kms=FakeKmsClient(),
            envelopes=MemoryEnvelopeStore(),
        )
        identity = _proposal("proposal:1")
        payload = await protection.protect_for(
            proposal_identity=identity,
            canonical_payload=b'{"account":"private"}',
        )

        corrupted = payload.model_copy(
            update={
                "ciphertext": base64.b64encode(
                    _flip_last_byte(base64.b64decode(payload.ciphertext, validate=True))
                ).decode("ascii")
            }
        )
        with pytest.raises(ValueError, match="authentication failed"):
            await protection.unprotect_for(proposal_identity=identity, payload=corrupted)

        wrong_version = payload.model_copy(update={"key_version": "changed"})
        with pytest.raises(ValueError, match="metadata does not match"):
            await protection.unprotect_for(proposal_identity=identity, payload=wrong_version)

    asyncio.run(scenario())


def test_equivalent_kms_alias_cannot_replace_the_bound_resolved_key_id() -> None:
    async def scenario() -> None:
        envelopes = MemoryEnvelopeStore()
        protection = AwsKmsEnvelopeProtection(
            key_id="alias/threvo-actions",
            kms=FakeKmsClient(),
            envelopes=envelopes,
        )
        payload_identity = _proposal("proposal:payload")
        payload = await protection.protect_for(
            proposal_identity=payload_identity,
            canonical_payload=b"private",
        )
        payload_envelope = envelopes.entries[payload.key_handle]
        envelopes.entries[payload.key_handle] = payload_envelope.model_copy(
            update={"key_id": "alias/threvo-actions"}
        )

        with pytest.raises(ValueError, match="authentication failed"):
            await protection.unprotect_for(
                proposal_identity=payload_identity,
                payload=payload,
            )

        commitment_identity = _proposal("proposal:commitment")
        commitment = await protection.create_for(
            proposal_identity=commitment_identity,
            canonical_payload=b"private",
        )
        commitment_envelope = envelopes.entries[commitment.key_handle]
        envelopes.entries[commitment.key_handle] = commitment_envelope.model_copy(
            update={"key_id": "alias/threvo-actions"}
        )

        assert not await protection.verify_for(
            proposal_identity=commitment_identity,
            canonical_payload=b"private",
            commitment=commitment,
        )

    asyncio.run(scenario())


def test_empty_payload_round_trips() -> None:
    async def scenario() -> None:
        protection = AwsKmsEnvelopeProtection(
            key_id="alias/threvo-actions",
            kms=FakeKmsClient(),
            envelopes=MemoryEnvelopeStore(),
        )

        identity = _proposal("proposal:empty")
        payload = await protection.protect_for(
            proposal_identity=identity,
            canonical_payload=b"",
        )

        assert await protection.unprotect_for(proposal_identity=identity, payload=payload) == b""

    asyncio.run(scenario())


def test_invalid_protected_payload_does_not_persist_an_orphaned_key() -> None:
    async def scenario() -> None:
        envelopes = MemoryEnvelopeStore()
        protection = AwsKmsEnvelopeProtection(
            key_id="alias/threvo-actions",
            kms=FakeKmsClient(),
            envelopes=envelopes,
        )

        with pytest.raises(ValidationError, match="at most 1048576"):
            await protection.protect_for(
                proposal_identity=_proposal("proposal:oversized"),
                canonical_payload=b"x" * 786_405,
            )

        assert envelopes.entries == {}

    asyncio.run(scenario())


def test_resolved_key_id_survives_alias_repointing() -> None:
    async def scenario() -> None:
        kms = FakeKmsClient()
        envelopes = MemoryEnvelopeStore()
        protection = AwsKmsEnvelopeProtection(
            key_id="alias/threvo-actions",
            kms=kms,
            envelopes=envelopes,
        )
        identity = _proposal("proposal:1")
        payload = await protection.protect_for(
            proposal_identity=identity,
            canonical_payload=b"private",
        )
        envelope = envelopes.entries[payload.key_handle]

        kms.repoint_alias()

        assert envelope.key_id.endswith("key/one")
        assert (
            await protection.unprotect_for(proposal_identity=identity, payload=payload)
            == b"private"
        )

    asyncio.run(scenario())


def test_configured_key_id_is_forwarded_to_generate_data_key() -> None:
    async def scenario() -> None:
        kms = FakeKmsClient()
        protection = AwsKmsEnvelopeProtection(
            key_id="alias/threvo-actions",
            kms=kms,
            envelopes=MemoryEnvelopeStore(),
        )

        await protection.protect_for(
            proposal_identity=_proposal("proposal:1"),
            canonical_payload=b"private",
        )

        assert kms.generated_key_ids == ["alias/threvo-actions"]

    asyncio.run(scenario())


def test_wrapped_key_json_round_trip_preserves_binary_ciphertext() -> None:
    envelope = WrappedDataKey(
        tenant_reference="tenant:test",
        proposal_reference="proposal:1",
        purpose="payload",
        key_id="arn:aws:kms:eu-west-1:123456789012:key/example",
        key_version="aws-kms:version",
        ciphertext=b"\xff\x00\x80wrapped",
    )

    assert WrappedDataKey.model_validate_json(envelope.model_dump_json()) == envelope


def test_supported_kms_module_contract_is_explicit() -> None:
    from threvo_actions.integrations import aws_kms

    assert set(aws_kms.__all__) == {
        "AwsKmsEnvelopeProtection",
        "GeneratedDataKey",
        "KmsDataKeyClient",
        "KmsKeyIdentifier",
        "WrappedDataKey",
        "WrappedDataKeyStore",
    }
    assert all(hasattr(aws_kms, name) for name in aws_kms.__all__)


@pytest.mark.parametrize("length", [256, 2048])
def test_aws_valid_long_key_identifiers_are_accepted(length: int) -> None:
    key_id = "a" * length

    AwsKmsEnvelopeProtection(
        key_id=key_id,
        kms=FakeKmsClient(),
        envelopes=MemoryEnvelopeStore(),
    )


def test_key_identifiers_over_the_aws_limit_are_rejected() -> None:
    with pytest.raises(ValueError, match="2048"):
        AwsKmsEnvelopeProtection(
            key_id="a" * 2049,
            kms=FakeKmsClient(),
            envelopes=MemoryEnvelopeStore(),
        )


def test_commitment_is_bound_to_the_proposal_and_stored_envelope() -> None:
    async def scenario() -> None:
        envelopes = MemoryEnvelopeStore()
        protection = AwsKmsEnvelopeProtection(
            key_id="arn:aws:kms:eu-west-1:123456789012:key/example",
            kms=FakeKmsClient(),
            envelopes=envelopes,
        )
        identity = _proposal("proposal:1")
        commitment = await protection.create_for(
            proposal_identity=identity,
            canonical_payload=b'{"account":"private"}',
        )

        assert not await protection.verify_for(
            proposal_identity=_proposal("proposal:2"),
            canonical_payload=b'{"account":"private"}',
            commitment=commitment,
        )
        await envelopes.delete(key_handle=commitment.key_handle)
        assert not await protection.verify_for(
            proposal_identity=identity,
            canonical_payload=b'{"account":"private"}',
            commitment=commitment,
        )

    asyncio.run(scenario())


def test_generated_plaintext_key_is_not_in_its_representation() -> None:
    generated = GeneratedDataKey(
        plaintext=bytearray(b"private-key-material"),
        ciphertext=b"wrapped-key-material",
        resolved_key_id="kms-key:version:1",
    )

    assert "private-key-material" not in repr(generated)
    assert "wrapped-key-material" not in repr(generated)


def test_whole_artifact_substitution_cannot_erase_another_proposal() -> None:
    async def scenario() -> None:
        envelopes = MemoryEnvelopeStore()
        protection = AwsKmsEnvelopeProtection(
            key_id="alias/threvo-actions",
            kms=FakeKmsClient(),
            envelopes=envelopes,
        )
        first_identity = _proposal("proposal:first")
        second_identity = _proposal("proposal:second")
        first = await protection.protect_for(
            proposal_identity=first_identity,
            canonical_payload=b"first",
        )
        second = await protection.protect_for(
            proposal_identity=second_identity,
            canonical_payload=b"second",
        )
        with pytest.raises(ValueError, match="metadata does not match"):
            await protection.destroy_payload_for(
                proposal_identity=first_identity,
                payload=second,
            )
        assert (
            await protection.unprotect_for(proposal_identity=first_identity, payload=first)
            == b"first"
        )
        assert (
            await protection.unprotect_for(proposal_identity=second_identity, payload=second)
            == b"second"
        )

        first_commitment = await protection.create_for(
            proposal_identity=first_identity,
            canonical_payload=b"first",
        )
        second_commitment = await protection.create_for(
            proposal_identity=second_identity,
            canonical_payload=b"second",
        )
        with pytest.raises(ValueError, match="metadata does not match"):
            await protection.destroy_commitment_for(
                proposal_identity=first_identity,
                commitment=second_commitment,
            )
        assert await protection.verify_for(
            proposal_identity=first_identity,
            canonical_payload=b"first",
            commitment=first_commitment,
        )
        assert await protection.verify_for(
            proposal_identity=second_identity,
            canonical_payload=b"second",
            commitment=second_commitment,
        )

    asyncio.run(scenario())


def test_same_proposal_reference_in_two_tenants_cannot_cross_kms_boundary() -> None:
    async def scenario() -> None:
        envelopes = MemoryEnvelopeStore()
        kms = FakeKmsClient()
        protection = AwsKmsEnvelopeProtection(
            key_id="alias/threvo-actions",
            kms=kms,
            envelopes=envelopes,
        )
        first_identity = ProposalIdentity(
            tenant_reference="tenant:first",
            proposal_reference="proposal:shared",
        )
        second_identity = ProposalIdentity(
            tenant_reference="tenant:second",
            proposal_reference="proposal:shared",
        )
        first_payload = await protection.protect_for(
            proposal_identity=first_identity,
            canonical_payload=b"first",
        )
        second_payload = await protection.protect_for(
            proposal_identity=second_identity,
            canonical_payload=b"second",
        )

        with pytest.raises(ValueError, match="metadata does not match"):
            await protection.destroy_payload_for(
                proposal_identity=first_identity,
                payload=second_payload,
            )

        assert (
            await protection.unprotect_for(
                proposal_identity=first_identity,
                payload=first_payload,
            )
            == b"first"
        )
        assert (
            await protection.unprotect_for(
                proposal_identity=second_identity,
                payload=second_payload,
            )
            == b"second"
        )
        assert {context["threvo-actions:tenant"] for context in kms.contexts} == {
            "tenant:first",
            "tenant:second",
        }

    asyncio.run(scenario())


def test_plaintext_data_key_is_zeroed_before_store_failure_escapes() -> None:
    canary = b"0123456789abcdef0123456789abcdef"

    class FailingEnvelopeStore(MemoryEnvelopeStore):
        async def put(self, *, key_handle: str, envelope: WrappedDataKey) -> None:
            del key_handle, envelope
            raise RuntimeError("store unavailable")

    async def scenario() -> None:
        protection = AwsKmsEnvelopeProtection(
            key_id="alias/threvo-actions",
            kms=FakeKmsClient(plaintext=canary),
            envelopes=FailingEnvelopeStore(),
        )

        with pytest.raises(RuntimeError) as caught:
            await protection.protect_for(
                proposal_identity=_proposal("proposal:1"),
                canonical_payload=b"private",
            )

        traceback = caught.value.__traceback__
        while traceback is not None:
            if "/threvo_actions/" in traceback.tb_frame.f_code.co_filename:
                for value in traceback.tb_frame.f_locals.values():
                    if isinstance(value, bytearray):
                        assert bytes(value) != canary
                    if isinstance(value, GeneratedDataKey):
                        assert bytes(value.plaintext) != canary
            traceback = traceback.tb_next

    asyncio.run(scenario())


def test_decrypted_data_key_is_zeroed_on_authentication_failure() -> None:
    canary = b"0123456789abcdef0123456789abcdef"

    async def scenario() -> None:
        kms = FakeKmsClient(plaintext=canary)
        protection = AwsKmsEnvelopeProtection(
            key_id="alias/threvo-actions",
            kms=kms,
            envelopes=MemoryEnvelopeStore(),
        )
        identity = _proposal("proposal:1")
        payload = await protection.protect_for(
            proposal_identity=identity,
            canonical_payload=b"private",
        )
        corrupted = payload.model_copy(
            update={
                "ciphertext": base64.b64encode(
                    _flip_last_byte(base64.b64decode(payload.ciphertext, validate=True))
                ).decode("ascii")
            }
        )

        with pytest.raises(ValueError) as caught:
            await protection.unprotect_for(proposal_identity=identity, payload=corrupted)

        traceback = caught.value.__traceback__
        while traceback is not None:
            if "/threvo_actions/" in traceback.tb_frame.f_code.co_filename:
                for value in traceback.tb_frame.f_locals.values():
                    if isinstance(value, bytearray):
                        assert bytes(value) != canary
            traceback = traceback.tb_next
        assert kms.last_decrypted_plaintext == bytearray(32)

    asyncio.run(scenario())
