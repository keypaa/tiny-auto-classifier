#!/usr/bin/env python3
"""Review distinct secret spans: secret (redact) or keep (boilerplate).

One screen per span: pattern, how many rows it touches, the value itself.
Single keypress, no Enter needed on a TTY. Progress saves per decision;
re-running resumes. Decisions feed scripts/apply_redaction.py.

Keys: s=secret(redact)  k=keep  u=undo  q=quit

Your secrets are displayed on YOUR screen from YOUR files. Nothing leaves
the machine; do not paste values into chat.

Usage:
    python3 scripts/review_secrets.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def getkey(prompt: str = "") -> str:
    print(prompt, end="", flush=True)
    try:
        import termios
        import tty

        fd = sys.stdin.fileno()
        if not sys.stdin.isatty():
            raise OSError("not a tty")
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print(ch)
        if ch == "\x03":
            raise KeyboardInterrupt
        return ch.lower()
    except Exception:
        try:
            line = input().strip().lower()
        except EOFError:
            return "q"
        return line[:1] if line else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spans", type=Path,
                    default=ROOT / "data" / "import" / "secret_spans.jsonl")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "data" / "import" / "secret_decisions.jsonl")
    args = ap.parse_args()

    if not args.spans.exists():
        sys.exit(f"missing {args.spans}\nrun this first:\n"
                 f"  python3 scripts/extract_secret_spans.py")
    spans = [json.loads(line) for line in args.spans.read_text().splitlines()
             if line.strip()]
    if not spans:
        sys.exit(f"{args.spans} is empty — nothing to review")
    done: dict[str, dict] = {}
    order: list[str] = []
    if args.out.exists():
        for row in args.out.read_text().splitlines():
            if row.strip():
                r = json.loads(row)
                done[r["span_id"]] = r
                order.append(r["span_id"])
    print(f"spans: {len(spans)}  decided: {len(done)}")

    def save() -> None:
        args.out.write_text("\n".join(json.dumps(done[k]) for k in order)
                            + ("\n" if order else ""), encoding="utf-8")

    pending = [s for s in spans if s["span_id"] not in done]
    print("keys: s=secret(redact)  k=keep  u=undo  q=quit\n")
    start = time.monotonic()
    n = 0
    idx = 0
    while idx < len(pending):
        s = pending[idx]
        print("=" * 70)
        print(f"[{s['span_id']}] {idx+1}/{len(pending)} pending | "
              f"pattern={s['pattern']} | touches {s['rows']} rows")
        print(f"  value: {s['span'][:400]}")
        key = getkey("decision [s/k/u/q]: ")
        if key == "q":
            break
        if key == "u":
            if order:
                undone = order.pop()
                del done[undone]
                save()
                print(f"  undone {undone}")
                pending = [x for x in spans if x["span_id"] not in done]
                idx = next((i for i, x in enumerate(pending)
                            if x["span_id"] == undone), 0)
            else:
                print("  nothing to undo")
            continue
        if key not in ("s", "k"):
            print("  unknown key")
            continue
        done[s["span_id"]] = {"span_id": s["span_id"],
                              "decision": "secret" if key == "s" else "keep",
                              "pattern": s["pattern"],
                              "ts": time.strftime("%FT%TZ", time.gmtime())}
        order.append(s["span_id"])
        save()
        n += 1
        idx += 1
        print(f"  saved [{len(done)}/{len(spans)}]")
    save()
    el = time.monotonic() - start
    print(f"\ndone: {len(done)}/{len(spans)} -> {args.out} "
          f"({el:.0f}s this session)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())