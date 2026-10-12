#!/usr/bin/env python3
"""Extract distinct secret spans from converted pairs for human review.

The 5,858 flagged rows collapse to ~78 distinct spans (session boilerplate
repeats). Reviewing spans instead of rows is ~75x less work for the same
decision. Output stays under data/import/ (gitignored) because spans may
hold live secrets. Refuses to overwrite.

Usage:
    python3 scripts/extract_secret_spans.py
    python3 scripts/extract_secret_spans.py --in data/import/imported_all.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from convert_sessions import SECRET_PATTERNS  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="inp", type=Path,
                    default=ROOT / "data" / "import" / "imported_all.jsonl")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "data" / "import" / "secret_spans.jsonl")
    args = ap.parse_args()

    if args.out.exists():
        sys.exit(f"refusing to overwrite {args.out}")
    if not args.inp.exists():
        sys.exit(f"missing {args.inp}")

    spans: dict[str, dict] = {}
    n_rows = 0
    # Stream line by line: the input is ~3.4GB and read_text() + splitlines()
    # + json overhead previously peaked ~30GB. Peak now is one row.
    # Transcript and action are scanned separately to avoid doubling every
    # row with a concatenated copy.
    with open(args.inp, errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            n_rows += 1
            r = json.loads(line)
            seen_this_row: set[str] = set()
            for text in (r.get("transcript", ""), r.get("action", "")):
                for name, rx in SECRET_PATTERNS.items():
                    for m in rx.finditer(text):
                        span = m.group(0)
                        h = hashlib.sha1(span.encode()).hexdigest()[:12]
                        e = spans.get(h)
                        if e is None:
                            spans[h] = {"span_id": f"sp{len(spans)+1:03d}",
                                        "pattern": name, "span": span,
                                        "rows": 0, "hits": 0}
                            e = spans[h]
                        e["hits"] += 1
                        if h not in seen_this_row:
                            seen_this_row.add(h)
                            e["rows"] += 1
    ordered = sorted(spans.values(), key=lambda e: -e["rows"])
    for i, e in enumerate(ordered, 1):
        e["span_id"] = f"sp{i:03d}"
    args.out.write_text("\n".join(json.dumps(e, ensure_ascii=False)
                                  for e in ordered) + "\n", encoding="utf-8")
    print(f"rows scanned: {n_rows}")
    print(f"distinct spans: {len(ordered)} -> {args.out}")
    print("by pattern:")
    from collections import Counter
    print(" ", dict(Counter(e["pattern"] for e in ordered)))
    print("top 5 by row frequency:")
    for e in ordered[:5]:
        print(f"  {e['span_id']} [{e['pattern']}] rows={e['rows']}")
    print("\nWARNING: this file may hold live secrets. It lives under "
          "data/import/ (gitignored). Do not paste its contents into chat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())