"""Collect optional integration suites only when their extras are installed."""

from __future__ import annotations

import importlib


def _provides(module_name: str, attribute: str | None = None) -> bool:
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return False
    return attribute is None or hasattr(module, attribute)


collect_ignore: list[str] = []
if not _provides("cryptography.hazmat.primitives.ciphers.aead", "AESGCM"):
    collect_ignore.append("aws_kms")
if not _provides("asyncpg"):
    collect_ignore.append("postgres")
if not _provides("aiomysql"):
    collect_ignore.append("mysql")
if not _provides("pydantic_ai", "Agent"):
    collect_ignore.append("pydantic_ai")
