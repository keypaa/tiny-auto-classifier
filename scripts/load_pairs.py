#!/usr/bin/env python3
"""Read captured auto-mode decision pairs, normalising schema versions.

The capture schema changed once already: `suffix_const` became
`stage1_suffix_const` / `stage2_suffix_const`, because a single field cannot say
which stage a suffix belonged to. On the first capture every record had
`suffix_const: "QXo"` while stage 2 was demonstrably emitting `<severity>`, which
per the `s7o` matrix means stage 2 ran with `JXo`. The field was ambiguous, so it
was split.

This loader reads both the old and new shape so historical captures stay usable.

Usage:
    python3 scripts/load_pairs.py data/capture/2026-10-05
    python3 scripts/load_pairs.py data/capture/2026-10-05 --summary
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

SCHEMA = "tiny-auto-classifier/pairs/1"


def normalise(record: dict) -> dict:
    """Bring one record up to the current field shape."""
    out = dict(record)

    # Split the ambiguous single suffix field. Newer captures write the split
    # fields alongside the legacy one; only flag ambiguity when the split
    # fields are absent, rather than inventing an assignment that would
    # silently corrupt analysis.
    out.setdefault("stage1_suffix_const", None)
    out.setdefault("stage2_suffix_const", None)
    single = out.pop("suffix_const", None)
    if single is not None and not (
        out["stage1_suffix_const"] or out["stage2_suffix_const"]
    ):
        out["suffix_const_ambiguous"] = single
    else:
        out.pop("suffix_const_ambiguous", None)

    # Retry attempts were not logged on the first capture, which made an empty
    # stage-1 response impossible to attribute to capture vs model behaviour.
    out.setdefault("attempts", None)
    out.setdefault("thinking_present", None)

    out.setdefault("schema", SCHEMA)
    return out


def iter_records(root: Path) -> Iterator[dict]:
    for path in sorted(root.rglob("*.jsonl")):
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield normalise(json.loads(line))
                except json.JSONDecodeError as exc:
                    print(f"{path}:{lineno}: bad JSON: {exc}", file=sys.stderr)


def summarize(records: list[dict]) -> dict:
    def dig(rec: dict, *path: Any, default=None):
        node: Any = rec
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node if node is not None else default

    n = len(records)
    blocks = [r for r in records if dig(r, "final", "should_block")]
    allows = n - len(blocks)
    sevs = [
        v
        for r in records
        if (v := dig(r, "stage1", "parsed_severity")) is not None
    ]
    return {
        "records": n,
        "block": len(blocks),
        "allow": allows,
        "block_rate": round(len(blocks) / n, 4) if n else None,
        "mode_path": dict(Counter(dig(r, "mode_path", default="?") for r in records)),
        "variant": dict(Counter(str(r.get("variant")) for r in records)),
        "models": dict(Counter(str(r.get("classifier_model")) for r in records)),
        "thresholds": sorted({str(r.get("thresholds")) for r in records}),
        "categories": dict(
            Counter(
                str(dig(r, "final", "category_slug", default="none"))
                for r in records
            )
        ),
        "decision_source": dict(Counter(str(r.get("decision_source")) for r in records)),
        "empty_stage1": sum(
            1
            for r in records
            if dig(r, "stage1", "ran") and not (dig(r, "stage1", "response_raw") or "").strip()
        ),
        "stage1_severity_parsed": len(sevs),
        "severity_range": (
            [min(sevs), max(sevs)] if sevs else None
        ),
        "prompt_chars": sorted({r.get("system_prompt_chars") for r in records}),
        "suffix_ambiguous": sum(1 for r in records if r.get("suffix_const_ambiguous")),
        "suffix_stage1": dict(
            Counter(str(r.get("stage1_suffix_const")) for r in records)
        ),
        "suffix_stage2": dict(
            Counter(str(r.get("stage2_suffix_const")) for r in records)
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", type=Path, help="directory containing *.jsonl captures")
    ap.add_argument("--summary", action="store_true", help="print a summary instead of records")
    args = ap.parse_args()

    if not args.root.exists():
        print(f"not found: {args.root}", file=sys.stderr)
        return 2

    records = list(iter_records(args.root))
    if not records:
        print(f"no records under {args.root}", file=sys.stderr)
        return 1

    if not args.summary:
        for rec in records:
            print(json.dumps(rec, ensure_ascii=False))
        return 0

    s = summarize(records)
    print(json.dumps(s, indent=2, ensure_ascii=False))

    print("\n--- gaps we care about ---", file=sys.stderr)
    if s["empty_stage1"]:
        print(
            f"  {s['empty_stage1']}/{s['records']} records have an empty stage-1 "
            "response; stage 2 is doing all the work",
            file=sys.stderr,
        )
    if len(s["categories"]) <= 2:
        print("  no category-bearing (blocked) records yet", file=sys.stderr)
    if s["suffix_ambiguous"]:
        print(
            f"  {s['suffix_ambiguous']} records carry the pre-split suffix_const; "
            "cannot attribute suffix to stage",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())