"""Single source of truth — lean, no hydra/overengineering."""
from __future__ import annotations

import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Mode = Literal["encoder", "decoder"]
Precision = Literal["bf16", "fp16", "fp32"]


@dataclass
class LoraConfig:
    enabled: bool = True
    r: int = 16
    alpha: int = 32
    dropout: float = 0.05
    # None → auto-select per model type (see sft.py)
    target_modules: list[str] | None = None


@dataclass
class TrainingConfig:
    # model
    model_id: str = "ProCreations/auto-0.4b"
    mode: Mode = "encoder"  # encoder=SequenceClassification, decoder=CausalLM
    max_length: int = 8192  # curriculum steps: 8192→16384→27110→32768
    # data
    train_file: str = "data/raw/pilot_stub.jsonl"
    val_file: str | None = None
    policy_path: str = "prompts/original/policy.txt"
    # optimization — fits T4 12GB
    per_device_batch: int = 1  # T4 can't do more at 27K; use grad accum
    grad_accum: int = 8
    epochs: int = 1  # pilot: 1 epoch is enough to prove learnability
    lr: float = 2e-5
    weight_decay: float = 0.01
    warmup_ratio: float = 0.06
    optimizer: str = "adamw_8bit"  # bitsandbytes 8bit on T4, fallback to adamw
    precision: Precision = "bf16"  # T4: bf16 if available else fp16
    grad_checkpointing: bool = True
    # lora
    lora: LoraConfig = field(default_factory=LoraConfig)
    # runtime
    output_dir: str = "models/checkpoints/pilot"
    seed: int = 42
    num_workers: int = 2  # Colab 4 cores → 2 is safe
    logging_steps: int = 10
    save_steps: int = 200
    max_steps: int = -1  # -1 = full epoch; set 10 for smoke

    @classmethod
    def from_yaml(cls, path: str | Path) -> "TrainingConfig":
        raw = yaml.safe_load(Path(path).read_text())
        # nested lora
        if "lora" in raw and isinstance(raw["lora"], dict):
            raw["lora"] = LoraConfig(**raw["lora"])
        # filter unknown keys (forward-compat)
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in raw.items() if k in allowed}
        return cls(**filtered)

    @property
    def effective_batch(self) -> int:
        return self.per_device_batch * self.grad_accum

    def validate(self) -> None:
        assert self.mode in ("encoder", "decoder"), f"mode {self.mode}"
        assert self.max_length in (8192, 16384, 24576, 27110, 32768, 49152, 65536) or self.max_length > 0
        assert self.per_device_batch >= 1
        assert self.output_dir
        # 27K+ on T4 only with lora+checkpointing
        if self.max_length >= 24000 and not self.grad_checkpointing:
            raise ValueError("max_length >=24K requires grad_checkpointing on T4")
        if self.max_length >= 24000 and not self.lora.enabled:
            # not fatal but warn — full fine-tune at 27K OOMs on 12GB
            import warnings

            warnings.warn("Full fine-tune at 27K likely OOMs on T4 12GB; enable lora")
