#!/usr/bin/env python3
"""Apply redaction decisions: replace secret spans with typed placeholders.

Reads decisions from review_secrets.py, replaces longest-first (substring
spans), blanket-redacts emails to [EMAIL], verifies zero residual secret
hits, writes the redacted pairs file. Refuses to overwrite.

Placeholder map keeps structure trainable: [AWS_KEY], [GITHUB_TOKEN],
[ANTHROPIC_KEY], [PRIVATE_KEY], [SLACK_TOKEN], [PASSWORD], [EMAIL].

Usage:
    python3 scripts/apply_redaction.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from convert_sessions import EMAIL_RE, SECRET_PATTERNS  # noqa: E402

PLACEHOLDERS = {
    "aws_key": "[AWS_KEY]",
    "github_token": "[GITHUB_TOKEN]",
    "anthropic_key": "[ANTHROPIC_KEY]",
    "private_key": "[PRIVATE_KEY]",
    "slack_token": "[SLACK_TOKEN]",
    "password_assign": "[PASSWORD]",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="inp", type=Path,
                    default=ROOT / "data" / "import" / "imported_all.jsonl")
    ap.add_argument("--decisions", type=Path,
                    default=ROOT / "data" / "import" / "secret_decisions.jsonl")
    ap.add_argument("--spans", type=Path,
                    default=ROOT / "data" / "import" / "secret_spans.jsonl")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "data" / "import" / "imported_redacted.jsonl")
    args = ap.parse_args()

    if args.out.exists():
        sys.exit(f"refusing to overwrite {args.out}")

    span_by_id = {}
    for line in args.spans.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            span_by_id[r["span_id"]] = r
    decisions = {}
    for line in args.decisions.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            decisions[r["span_id"]] = r["decision"]
    undecided = [sid for sid in span_by_id if sid not in decisions]
    if undecided:
        sys.exit(f"{len(undecided)} spans undecided (e.g. {undecided[:3]}); "
                 f"finish review_secrets.py first")

    repls = []
    for sid, dec in decisions.items():
        if dec == "secret":
            info = span_by_id[sid]
            repls.append((info["span"], PLACEHOLDERS[info["pattern"]]))
    repls.sort(key=lambda t: -len(t[0]))
    print(f"redacting {len(repls)} spans, "
          f"{sum(1 for d in decisions.values() if d == 'keep')} kept")

    import time
    # One compiled alternation: a single C pass over each row instead of one
    # memmem scan per span (10 spans x 3.4GB was the bottleneck that got this
    # killed). Longest-first so overlapping spans resolve to the longest.
    table = {}
    for span, ph in repls:
        table.setdefault(span, ph)
    # Empty alternation matches everywhere: skip substitution when nothing
    # was marked secret.
    span_rx = (re.compile("|".join(re.escape(s) for s in sorted(
        table, key=len, reverse=True))) if table else None)
    # Placeholders must not match secret patterns, or the residual check can
    # never reach zero. Assert once up front instead of discovering it late.
    for ph in set(table.values()) | {"[EMAIL]"}:
        assert not any(rx.search(ph) for rx in SECRET_PATTERNS.values()), ph

    tmp = args.out.with_suffix(args.out.suffix + ".tmp")
    n_rows = n_touched = 0
    email_total = 0
    residual = 0
    started = time.monotonic()
    # Stream: same 30GB lesson as extract (read_text on 3.4GB). Output goes
    # to tmp and is atomically renamed only on success, so a killed run
    # never blocks retry with a corrupt partial.
    with open(tmp, "w", encoding="utf-8") as fh:
        with open(args.inp, errors="replace") as src:
            for line in src:
                if not line.strip():
                    continue
                n_rows += 1
                if span_rx is not None:
                    line, n_sub = span_rx.subn(lambda m: table[m.group(0)], line)
                    n_touched += n_sub > 0
                # Emails blanket-redacted separately (structure-preserving).
                line, n_em = EMAIL_RE.subn("[EMAIL]", line.rstrip("\n"))
                email_total += n_em
                # Residual check inline on the redacted line: no second pass.
                if any(rx.search(line) for rx in SECRET_PATTERNS.values()):
                    residual += 1
                fh.write(line + "\n")
                if n_rows % 500 == 0:
                    el = time.monotonic() - started
                    print(f"  {n_rows} rows, {el:.0f}s "
                          f"({n_rows / el:.0f} rows/s)", flush=True)
    import os
    os.replace(tmp, args.out)
    el = time.monotonic() - started
    print(f"\nwrote {args.out}: {n_rows} rows, {n_touched} touched, "
          f"{email_total} emails redacted, {el:.0f}s")
    print(f"residual secret-pattern rows: {residual}")
    if residual:
        print("NONZERO — inspect before using; a span was missed.")
        return 1
    print("clean: no secret pattern survives (values never printed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())