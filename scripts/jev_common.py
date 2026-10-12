#!/usr/bin/env python3
"""Shared helpers for Jev probes against the auto-mode rulebook.

Loads the resolved rulebook, extracts the 73 BLOCK rule entries, and builds
Jev requests in two payload shapes:

  A  full policy in `state`, rule names as `criteria`
  B  rule entries in `criteria`, evidence only in `state`

Shape B drops the 42% of the prompt that is reasoning procedure rather than
rules. See scripts/probe_jev.py for the shape-A baseline measurement.

Requires TYPESAFE_API_KEY in the environment for anything that sends a request.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://api.typesafe.ai/v1/systemone"
PRICE_PER_MTOK = 0.042
ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "stage2_verdict_prompt.txt"

BLOCK_HEADER = "## HARD BLOCK"
ALLOW_HEADER = "## ALLOW (exceptions)"


def api_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        sys.exit("TYPESAFE_API_KEY is not set")
    return key


def policy() -> str:
    return PROMPT_PATH.read_text()


def _slot_body(text: str, tag: str) -> str:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S)
    return match.group(1) if match else ""


def rule_entries(text: str | None = None) -> list[dict]:
    """Extract the 73 BLOCK rule entries: 1 HARD plus 72 SOFT.

    Each entry is `- Name: body` or `- Name [named+specifics, must name: X]: body`.
    Names are returned as the rulebook spells them; the policy instructs the
    model to emit them with punctuation stripped, which is handled by
    `emit_name`.
    """
    text = policy() if text is None else text
    out: list[dict] = []
    for section, tag in (
        ("HARD", "user_hard_deny_rules_to_replace"),
        ("SOFT", "user_soft_deny_rules_to_replace"),
    ):
        for line in _slot_body(text, tag).split("\n"):
            match = re.match(r"^- ([A-Za-z0-9][^:\[]{0,70}?)(?::|\s*\[)\s*(.*)$", line)
            if match:
                out.append(
                    {
                        "name": match.group(1).strip(),
                        "body": match.group(2).strip(),
                        "section": section,
                        "chars": len(line),
                    }
                )
    return out


def emit_name(name: str) -> str:
    """The form the policy requires inside <category>.

    Letters, digits and spaces only: replace / and - with a space, drop other
    punctuation. Q7 confirmed all 73 round-trip through this transform.
    """
    out = name.replace("/", " ").replace("-", " ")
    out = re.sub(r"[^\w\s]", "", out)
    return re.sub(r"\s+", " ", out).strip()


def rules_criteria(entries: list[dict], limit: int | None = None) -> dict:
    """Build a Choice criteria map: rule emit-name -> rule text."""
    items = entries if limit is None else entries[:limit]
    return {emit_name(r["name"]): r["body"] or r["name"] for r in items}


def evidence_block(transcript: str, action: str) -> str:
    return f"EVIDENCE\n{transcript}\nACTION\n{action}"


def request_body(state: str, questions: dict, model: str = "jev-latest") -> dict:
    return {"state": state, "model": model, "questions": questions}


def send(body: dict, timeout: int = 180) -> tuple[dict, float]:
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read()), time.monotonic() - started
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:2000]
        sys.exit(f"HTTP {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        sys.exit(f"network error: {exc.reason}")


def report(label: str, result: dict, elapsed: float) -> None:
    usage = result.get("usage", {})
    cost = usage.get("input_tokens", 0) / 1_000_000 * PRICE_PER_MTOK
    print(f"\n[{label}]  {result.get('model')}  {elapsed:.2f}s")
    print(
        f"  tokens in={usage.get('input_tokens', 0):,} "
        f"out={usage.get('output_tokens', 0):,}  cost=${cost:.5f}"
    )
    for qid, ans in result.get("answers", {}).items():
        parts = [f"{ans.get('type')}"]
        for key in ("noul", "score", "choice", "confidence"):
            if key in ans:
                parts.append(f"{key}={ans[key]}")
        print(f"  {qid}: {', '.join(parts)}")


def top_probabilities(ans: dict, n: int = 6) -> list[tuple[str, float]]:
    probs = ans.get("probabilities") or {}
    return sorted(probs.items(), key=lambda kv: -kv[1])[:n]