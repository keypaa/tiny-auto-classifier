"""Compatibility shim — see src/prompt/tokenizer_probe.py"""
from src.prompt.tokenizer_probe import probe_tokenizer, probe_many, TokenizationResult

__all__ = ["probe_tokenizer", "probe_many", "TokenizationResult"]
