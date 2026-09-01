"""Compatibility shim — see src/prompt/builder.py"""
from src.prompt.builder import PromptBuilder, PolicyHashMismatchError, PromptManifest, sha256_hex, sha256_file

__all__ = ["PromptBuilder", "PolicyHashMismatchError", "PromptManifest", "sha256_hex", "sha256_file"]
