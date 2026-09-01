#!/usr/bin/env python3
"""Check policy hash against manifest — ABORT BENCHMARK if mismatched."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.prompt.builder import PromptBuilder, PolicyHashMismatchError

policy = Path("prompts/original/policy.txt")
if not policy.exists():
    print(f"Missing {policy} — place exact 20-30K policy there")
    sys.exit(2)
try:
    pb = PromptBuilder(policy)
    pb.verify_policy()
    print(f"OK policy_hash={pb.policy_hash}")
    print(f"chars={len(pb.policy_text)}")
except PolicyHashMismatchError as e:
    print(e)
    sys.exit(1)
