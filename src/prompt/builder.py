"""
PromptBuilder — Canonical Prompt Replay (Roadmap §7).

Inputs:  policy, transcript, metadata, latest_action
Output:  exact classifier input (policy + rendered dynamic section)

For every prompt records:
  policy_hash  (SHA-256 of raw policy text)
  prompt_hash  (SHA-256 of full rendered prompt)
  characters
  tokens            (optional, requires tokenizer)
  dynamic_tokens    (optional)
  model_tokenizer   (optional)

Hard requirement: if policy_hash != manifest expected hash => ABORT BENCHMARK
(raises PolicyHashMismatchError — caller must fallback, never allow locally).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class PolicyHashMismatchError(RuntimeError):
    """Raised when policy hash does not match manifest. Caller must fallback (fail-closed)."""

    def __init__(self, expected: str, actual: str):
        super().__init__(
            f"ABORT BENCHMARK: policy hash mismatch — expected {expected}, got {actual}. "
            "Do not continue. Invoke fallback (fail-closed)."
        )
        self.expected = expected
        self.actual = actual


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class PromptManifest:
    policy_hash: str
    prompt_hash: str
    characters: int
    tokens: Optional[int] = None
    dynamic_tokens: Optional[int] = None
    model_tokenizer: Optional[str] = None
    policy_path: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


class PromptBuilder:
    """
    Canonical replay builder.

    Policy file is the ground truth. Its SHA-256 is compared against
    manifests/original/policy.sha256 (or passed expected_hash).
    """

    # Default template reproduces the Claude Code 2.1.289 wire format.
    #
    # The envelope is built by KXo() in the shipped bundle and is:
    #
    #     <transcript>\n{blocks}\n</transcript>\n{suffix}\n{action}
    #
    # The action follows the closing tag, not inside it. Evidence and action are
    # therefore separated by a delimiter the user cannot forge: NT()/nI() bracket
    # a user-supplied literal "<transcript>" into "[transcript>" on output. See
    # analysis/Q8-classifierMetaLines.md.
    #
    # `metadata` is not part of the 2.1.289 envelope. It is retained as an
    # optional trailing context block so callers can pass host facts without
    # breaking the format; it is ignored when empty.
    DEFAULT_DYNAMIC_TEMPLATE = (
        "<transcript>\n{transcript}</transcript>\n"
        "{metadata}{latest_action}"
    )

    # When metadata is present it must be its own block before the action, so the
    # suffix template needs a newline separator that only fires in that case.
    METADATA_TEMPLATE = "\n{metadata}\n"

    def __init__(
        self,
        policy_path: Path | str,
        expected_policy_hash: Optional[str] = None,
        dynamic_template: Optional[str] = None,
    ):
        self.policy_path = Path(policy_path)
        if not self.policy_path.exists():
            raise FileNotFoundError(f"Policy file not found: {self.policy_path}")
        self.policy_text = self.policy_path.read_text(encoding="utf-8")
        self.policy_hash = sha256_hex(self.policy_text)
        self.expected_policy_hash = expected_policy_hash
        # auto-load manifest hash if not passed explicitly
        if self.expected_policy_hash is None:
            manifest_hash_path = self.policy_path.parent.parent / "manifests" / "policy.sha256"
            # also check prompts/manifests/policy.sha256
            alt = Path("prompts/manifests/policy.sha256")
            if manifest_hash_path.exists():
                self.expected_policy_hash = manifest_hash_path.read_text().strip().split()[0]
            elif alt.exists():
                self.expected_policy_hash = alt.read_text().strip().split()[0]

        self.dynamic_template = dynamic_template or self.DEFAULT_DYNAMIC_TEMPLATE

    def verify_policy(self) -> None:
        """ABORT BENCHMARK if hash mismatches manifest. Fail-closed."""
        if self.expected_policy_hash and self.policy_hash != self.expected_policy_hash:
            raise PolicyHashMismatchError(self.expected_policy_hash, self.policy_hash)

    def build(
        self,
        transcript: str = "",
        metadata: str = "",
        latest_action: str = "",
        *,
        model_tokenizer: Optional[str] = None,
        tokenizer=None,
        verify: bool = True,
    ) -> tuple[str, PromptManifest]:
        """
        Build exact classifier input.

        Returns (prompt_text, manifest).
        If tokenizer is provided, manifest.tokens / dynamic_tokens are filled.
        """
        if verify:
            self.verify_policy()

        # metadata is optional in the 2.1.289 envelope; drop it entirely when
        # empty so the prompt matches the shipped bytes rather than carrying a
        # blank line the real system would not send.
        if metadata.strip():
            dynamic_section = self.dynamic_template.format(
                transcript=transcript,
                metadata=self.METADATA_TEMPLATE.format(metadata=metadata.strip()),
                latest_action=latest_action,
            )
        else:
            # No metadata: the shipped envelope has nothing between the closing
            # tag and the action.
            dynamic_section = self.dynamic_template.format(
                transcript=transcript, metadata="", latest_action=latest_action
            )
        prompt = self.policy_text + dynamic_section
        prompt_hash = sha256_hex(prompt)
        characters = len(prompt)

        tokens: Optional[int] = None
        dynamic_tokens: Optional[int] = None
        if tokenizer is not None:
            try:
                tokens = len(tokenizer.encode(prompt))
                dynamic_tokens = len(tokenizer.encode(dynamic_section))
            except Exception:
                # tokenizer may be HF tokenizer with different API
                try:
                    tokens = len(tokenizer(prompt)["input_ids"])
                    dynamic_tokens = len(tokenizer(dynamic_section)["input_ids"])
                except Exception:
                    pass

        manifest = PromptManifest(
            policy_hash=self.policy_hash,
            prompt_hash=prompt_hash,
            characters=characters,
            tokens=tokens,
            dynamic_tokens=dynamic_tokens,
            model_tokenizer=model_tokenizer,
            policy_path=str(self.policy_path),
        )
        return prompt, manifest

    def build_and_save_manifest(
        self,
        transcript: str = "",
        metadata: str = "",
        latest_action: str = "",
        manifest_path: Optional[Path | str] = None,
        **kwargs,
    ) -> tuple[str, PromptManifest]:
        prompt, manifest = self.build(transcript, metadata, latest_action, **kwargs)
        if manifest_path:
            Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
            Path(manifest_path).write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
        return prompt, manifest
