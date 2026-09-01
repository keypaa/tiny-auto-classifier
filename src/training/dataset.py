"""Dataset — builds exact classifier input via PromptBuilder, tokenizes for encoder/decoder."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import torch
from torch.utils.data import Dataset

from src.prompt.builder import PromptBuilder, PolicyHashMismatchError


class SFTDataset(Dataset):
    """
    Reads JSONL with fields: transcript, latest_action, label (ALLOW/BLOCK), optional rule_id.
    Builds prompt = policy + transcript + metadata + latest_action via PromptBuilder (hash-checked).
    Tokenizes to max_length. No silent truncation beyond max_length — sample is hard-truncated
    and flagged (caller must treat as overflow→fallback at eval, but training still sees it).

    Modes:
      encoder: returns {input_ids, attention_mask, labels: 0/1}
      decoder: returns {input_ids, attention_mask, labels: -100 …<target ids>} for "ALLOW" / "BLOCK:R17"
    """

    def __init__(
        self,
        jsonl_path: str | Path,
        tokenizer,
        policy_path: str | Path = "prompts/original/policy.txt",
        mode: Literal["encoder", "decoder"] = "encoder",
        max_length: int = 8192,
        verify_policy: bool = True,
    ):
        self.path = Path(jsonl_path)
        if not self.path.exists():
            raise FileNotFoundError(f"Dataset not found: {self.path}")
        self.tokenizer = tokenizer
        self.mode = mode
        self.max_length = max_length
        self.builder = PromptBuilder(policy_path)
        if verify_policy:
            self.builder.verify_policy()  # fail-closed at data load

        self.records = [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]
        if not self.records:
            raise ValueError(f"Empty dataset: {self.path}")

        # pre-check label distribution (fail fast if 90% ALLOW)
        from collections import Counter

        c = Counter(r.get("label", "UNKNOWN") for r in self.records)
        if c["ALLOW"] / len(self.records) > 0.85:
            import warnings

            warnings.warn(f"Dataset {self.path} is {c['ALLOW']/len(self.records):.0%} ALLOW — violates §12 balance")

    def __len__(self):
        return len(self.records)

    def __getitem__(self, idx: int) -> dict:
        r = self.records[idx]
        transcript = r.get("transcript", "")
        latest_action = r.get("latest_action", "")
        metadata = r.get("metadata", "")
        label = r.get("label", "ALLOW")  # ALLOW | BLOCK
        rule_id = r.get("rule_id", "R17") if label == "BLOCK" else None

        prompt, manifest = self.builder.build(
            transcript=transcript, metadata=metadata, latest_action=latest_action, verify=False
        )
        # flag over-length (prompt_builder already hashes, but we note truncation)
        truncated = manifest.characters > self.max_length * 4  # heuristic, real check after tokenization

        enc = self.tokenizer(
            prompt,
            truncation=True,
            max_length=self.max_length,
            padding=False,  # collator pads
            return_attention_mask=True,
        )
        input_ids = enc["input_ids"]
        attention_mask = enc["attention_mask"]
        # real truncation check: if tokenizer truncated to max_length, input_ids == max_length
        if len(input_ids) == self.max_length:
            truncated = True

        if self.mode == "encoder":
            # 0=approve/ALLOW, 1=deny/BLOCK per auto-0.4b id2label
            cls_label = 0 if label == "ALLOW" else 1
            return {
                "input_ids": torch.tensor(input_ids, dtype=torch.long),
                "attention_mask": torch.tensor(attention_mask, dtype=torch.long),
                "labels": torch.tensor(cls_label, dtype=torch.long),
                "truncated": truncated,
            }
        else:
            # decoder: train only on final decision tokens ("ALLOW" or "BLOCK:R17")
            # input = prompt + " " + target, labels = -100 for prompt, ids for target
            target = "ALLOW" if label == "ALLOW" else f"BLOCK:{rule_id}"
            # ensure target tokenization is stable (no extra spaces)
            tgt_ids = self.tokenizer(target, add_special_tokens=False)["input_ids"]
            # prompt already tokenized; concat
            full_ids = input_ids + tgt_ids
            # truncate from left if overflow (keep prompt tail + target) — rare, but deterministic
            if len(full_ids) > self.max_length:
                # keep last max_length tokens so target is always visible
                full_ids = full_ids[-self.max_length :]
                # labels need to align: only target part is supervised
                # we have to recompute prompt/target boundary after truncation
                # simplest: label all but last len(tgt_ids) as -100 (approx, avoids off-by-one)
                labels = [-100] * (len(full_ids) - len(tgt_ids)) + tgt_ids
            else:
                labels = [-100] * len(input_ids) + tgt_ids
            # attention mask for full
            attn = [1] * len(full_ids)
            return {
                "input_ids": torch.tensor(full_ids, dtype=torch.long),
                "attention_mask": torch.tensor(attn, dtype=torch.long),
                "labels": torch.tensor(labels, dtype=torch.long),
                "truncated": truncated,
            }


def collate_encoder(batch: list[dict], pad_token_id: int | None = None) -> dict:
    """Pad to longest in batch (not max_length) — saves VRAM at 27K."""
    input_ids = [b["input_ids"] for b in batch]
    masks = [b["attention_mask"] for b in batch]
    labels = torch.stack([b["labels"] for b in batch])
    max_len = max(len(x) for x in input_ids)
    # ModernBERT pad is 50283, not 0 — caller must pass tokenizer.pad_token_id
    if pad_token_id is None:
        import warnings

        warnings.warn("collate_encoder called without pad_token_id — defaulting to 50283 (ModernBERT). Pass tokenizer.pad_token_id.")
        pad_token_id = 50283
    padded_ids = torch.full((len(batch), max_len), pad_token_id, dtype=torch.long)
    padded_mask = torch.zeros((len(batch), max_len), dtype=torch.long)
    for i, (ids, m) in enumerate(zip(input_ids, masks)):
        padded_ids[i, : len(ids)] = ids
        padded_mask[i, : len(m)] = m
    return {"input_ids": padded_ids, "attention_mask": padded_mask, "labels": labels}


def collate_decoder(batch: list[dict], pad_token_id: int = 0) -> dict:
    max_len = max(len(b["input_ids"]) for b in batch)
    bsz = len(batch)
    ids = torch.full((bsz, max_len), pad_token_id, dtype=torch.long)
    mask = torch.zeros((bsz, max_len), dtype=torch.long)
    labels = torch.full((bsz, max_len), -100, dtype=torch.long)
    for i, b in enumerate(batch):
        l = len(b["input_ids"])
        ids[i, :l] = b["input_ids"]
        mask[i, :l] = b["attention_mask"]
        # labels may be shorter (prompt part -100); align left
        ll = len(b["labels"])
        labels[i, :ll] = b["labels"]
    return {"input_ids": ids, "attention_mask": mask, "labels": labels}
