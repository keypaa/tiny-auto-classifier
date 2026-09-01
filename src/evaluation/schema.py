"""
Benchmark schema — machine-readable results (Roadmap §10, §49).

Every experiment must produce machine-readable JSON; reports are generated,
never manually copied.

Covers:
  - Raw baseline matrix row
  - Experiment config / metrics / model_info / hardware / tokenization
  - Predictions / failures jsonl
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Literal


@dataclass
class BaselineRow:
    """Roadmap §10: raw baseline matrix entry."""
    model: str
    context: int
    accuracy: float
    false_allow: float
    false_deny: float
    rule_accuracy: Optional[float] = None
    latency_p50_ms: float = 0
    latency_p95_ms: float = 0
    latency_p99_ms: Optional[float] = None
    rss_mb: float = 0
    disk_mb: Optional[float] = None
    quantization: str = "fp32"
    notes: Optional[str] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class ExperimentConfig:
    experiment_id: str
    model: str
    revision: Optional[str] = None
    dataset_version: Optional[str] = None
    policy_hash: Optional[str] = None
    tokenizer_version: Optional[str] = None
    context_length: int = 27110
    quantization: str = "fp32"
    runtime: str = "pytorch-cpu"
    thread_count: int = 1
    seed: int = 42
    extra: dict = field(default_factory=dict)


@dataclass
class Metrics:
    accuracy: float = 0.0
    false_allow: float = 0.0
    false_deny: float = 0.0
    rule_accuracy: Optional[float] = None
    hard_block_recall: Optional[float] = None
    latency_p50_ms: float = 0
    latency_p95_ms: float = 0
    latency_p99_ms: Optional[float] = None
    rss_mb: float = 0
    disk_mb: Optional[float] = None
    # threshold / cascade
    threshold_low: Optional[float] = None
    threshold_high: Optional[float] = None
    local_coverage: Optional[float] = None
    fallback_rate: Optional[float] = None


@dataclass
class FailureRecord:
    """Roadmap §35: every error becomes structured record."""
    sample_id: str
    model: str
    checkpoint: str
    quantization: str
    context_length: int
    prediction: str
    ground_truth: str
    confidence: Optional[float] = None
    predicted_rule: Optional[str] = None
    ground_truth_rule: Optional[str] = None
    failure_category: str = "unknown"
    severity: str = "unknown"
    notes: Optional[str] = None


VALID_FAILURE_CATEGORIES = {
    "keyword trap", "semantic confusion", "consent confusion", "scope escalation",
    "provenance failure", "latest-action failure", "long-context forgetting",
    "hard/soft confusion", "rule confusion", "generalization failure",
    "prompt injection", "obfuscation", "wrapper execution", "browser", "git",
    "shared infrastructure", "destination",
}

# Helpers to write experiment artifacts (Roadmap §49)
REQUIRED_ARTIFACTS = [
    "config.json", "metrics.json", "model_info.json",
    "hardware.json", "tokenization.json", "predictions.jsonl", "failures.jsonl", "README.md"
]

def write_json(path: Path | str, data: dict | list):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def append_jsonl(path: Path | str, record: dict):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
