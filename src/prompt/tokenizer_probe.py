"""
Tokenizer probe — measures prompt in tokens for multiple tokenizers (Roadmap §7, §6.1).

For each tokenizer records:
  characters, tokens, dynamic_tokens, model_tokenizer

Never trusts model card context length; measures actual encoding.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class TokenizationResult:
    model_tokenizer: str
    characters: int
    tokens: int
    dynamic_tokens: Optional[int] = None
    error: Optional[str] = None


def probe_tokenizer(
    prompt: str,
    dynamic_section: str,
    tokenizer_name: str,
    trust_remote_code: bool = False,
) -> TokenizationResult:
    """Probe a single HF tokenizer without downloading model weights if possible."""
    try:
        from transformers import AutoTokenizer

        tok = AutoTokenizer.from_pretrained(tokenizer_name, trust_remote_code=trust_remote_code)
        tokens = len(tok.encode(prompt))
        dyn_tokens = len(tok.encode(dynamic_section))
        return TokenizationResult(
            model_tokenizer=tokenizer_name,
            characters=len(prompt),
            tokens=tokens,
            dynamic_tokens=dyn_tokens,
        )
    except Exception as e:
        return TokenizationResult(
            model_tokenizer=tokenizer_name,
            characters=len(prompt),
            tokens=-1,
            error=str(e)[:500],
        )


def probe_many(
    prompt: str,
    dynamic_section: str,
    tokenizer_names: list[str],
) -> list[TokenizationResult]:
    return [probe_tokenizer(prompt, dynamic_section, name) for name in tokenizer_names]


def probe_to_manifest_dict(prompt: str, dynamic_section: str, tokenizer_name: str) -> dict:
    r = probe_tokenizer(prompt, dynamic_section, tokenizer_name)
    return asdict(r)


# CLI helper
if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Tokenizer probe")
    p.add_argument("--prompt-file", type=str, required=True)
    p.add_argument("--tokenizers", nargs="+", default=["bert-base-uncased"])
    p.add_argument("--out", type=str, default=None)
    args = p.parse_args()
    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    # naive split: last ~2k chars as dynamic
    dynamic = prompt[-2000:]
    results = probe_many(prompt, dynamic, args.tokenizers)
    out = [asdict(r) for r in results]
    print(json.dumps(out, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2))
