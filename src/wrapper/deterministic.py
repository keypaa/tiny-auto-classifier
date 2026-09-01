"""
Deterministic output wrapper + fail-closed behavior (Roadmap §42, §41, §43).

Model returns structured: {decision, rule_id, confidence}
Wrapper maps to:
  ALLOW -> <block>no</block>
  BLOCK -> <block>yes</block><category>Exact Rule Name</category><reason>...</reason>

Fail-closed: malformed prediction, unknown rule, NaN confidence, timeout,
            runtime error, policy hash mismatch, context overflow -> fallback.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# Example rule map — must be replaced with exact policy rule names when policy is loaded.
# Keeping placeholder so wrapper is testable without real policy.
EXAMPLE_RULE_MAP = {
    "R01": "Rule 01 — Example",
    "R17": "Example Dangerous Action",
}

FALLBACK_SENTINEL = "FALLBACK"


@dataclass
class ModelPrediction:
    decision: str  # "ALLOW" | "BLOCK"
    rule_id: Optional[str] = None
    confidence: Optional[float] = None


@dataclass
class WrapperResult:
    xml: Optional[str]
    fallback: bool
    fallback_reason: Optional[str] = None


def is_valid_prediction(pred: ModelPrediction, rule_map: dict) -> tuple[bool, str]:
    if pred.decision not in ("ALLOW", "BLOCK"):
        return False, f"invalid decision: {pred.decision}"
    if pred.confidence is not None:
        if not isinstance(pred.confidence, (int, float)) or math.isnan(pred.confidence) or math.isinf(pred.confidence):
            return False, "invalid confidence (NaN/Inf)"
        if not (0.0 <= pred.confidence <= 1.0):
            return False, f"confidence out of range: {pred.confidence}"
    if pred.decision == "BLOCK":
        if not pred.rule_id or pred.rule_id == "UNKNOWN":
            return False, "BLOCK requires known rule_id"
        if pred.rule_id not in rule_map:
            return False, f"unknown rule_id: {pred.rule_id}"
    return True, ""


def wrap(pred: ModelPrediction, rule_map: dict = EXAMPLE_RULE_MAP) -> WrapperResult:
    ok, reason = is_valid_prediction(pred, rule_map)
    if not ok:
        return WrapperResult(xml=None, fallback=True, fallback_reason=reason)

    if pred.decision == "ALLOW":
        return WrapperResult(xml="<block>no</block>", fallback=False)

    # BLOCK
    rule_name = rule_map[pred.rule_id]
    xml = (
        f"<block>yes</block>\n"
        f"<category>{rule_name}</category>\n"
        f"<reason>[{rule_name}] Blocked by {pred.rule_id} (confidence {pred.confidence:.3f})</reason>"
        if pred.confidence is not None
        else f"<block>yes</block>\n<category>{rule_name}</category>\n<reason>[{rule_name}] Blocked by {pred.rule_id}</reason>"
    )
    return WrapperResult(xml=xml, fallback=False)


def fail_closed(reason: str) -> WrapperResult:
    """Any failure mode must NOT allow locally — return fallback."""
    return WrapperResult(xml=None, fallback=True, fallback_reason=reason)
