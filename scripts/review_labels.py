#!/usr/bin/env python3
"""Hand-label the review queue. One screen per pair, keystroke verdicts.

Keys (single keypress, no Enter needed on a TTY):
    o  accept the pre-filled verdict as-is (fast path)
    a  mark ALLOW
    b  mark BLOCK, then pick a category
    s  skip (leave unlabeled, move on)
    u  undo last label
    q  quit (progress is saved continuously)
    ?  show this help

Category picker: shows the pre-filled category as default (Enter accepts),
otherwise type a few letters to filter the 73 rule names, then the number.

Usage:
    python3 scripts/review_labels.py
    python3 scripts/review_labels.py --queue data/review_queue.jsonl --out data/hand_labels.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from jev_common import emit_name, rule_entries  # noqa: E402

WIDTH = 100


def getkey(prompt: str = "") -> str:
    """Single keypress; falls back to line input when stdin is not a TTY."""
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


def slugify(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def render_block(title: str, text: str, prefix: str = "  ") -> None:
    print(f"--- {title} ---")
    for line in text.split("\n"):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and len(obj) == 1:
                k, v = next(iter(obj.items()))
                line = f"{k}: {v}"
        except (json.JSONDecodeError, ValueError):
            pass
        for w in textwrap.wrap(line, WIDTH - len(prefix)) or [""]:
            print(f"{prefix}{w}")


def pick_category(cats: list[tuple[str, str, str]],
                  default: str | None) -> str | None:
    """cats: (slug, emit_name, rule_hint). Returns slug or None on abort."""
    names = {s: n for s, n, _ in cats}
    if default and default in names:
        hint = next((h for s, n, h in cats if s == default), "")
        print(f"  rule hint: {hint[:220]}")
        ans = input(f"  category [Enter={names[default]}]: ").strip()
        if not ans:
            return default
    else:
        ans = input("  category (type to filter): ").strip()
        if not ans and default:
            return default
    filt = ans
    if filt.isdigit() and 1 <= int(filt) <= len(cats):
        return cats[int(filt) - 1][0]
    matches = [(s, n) for s, n, _ in cats
               if filt.lower() in n.lower() or filt.lower() in s]
    if not matches:
        print("  no match — try again (empty aborts)")
        retry = input("  category: ").strip()
        return default if not retry else pick_category(cats, default)
    if len(matches) == 1:
        s, n = matches[0]
        print(f"  -> {n}")
        return s
    for i, (s, n) in enumerate(matches[:15], 1):
        print(f"  {i:2d}. {n}")
    while True:
        sel = input(f"  number (1-{min(15, len(matches))}, empty aborts): ").strip()
        if not sel:
            return default
        if sel.isdigit() and 1 <= int(sel) <= min(15, len(matches)):
            return matches[int(sel) - 1][0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", type=Path, default=ROOT / "data" / "review_queue.jsonl")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "hand_labels.jsonl")
    args = ap.parse_args()

    queue = [json.loads(line) for line in args.queue.read_text().splitlines() if line.strip()]
    done: dict[str, dict] = {}
    order: list[str] = []
    if args.out.exists():
        for line in args.out.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["qid"]] = r
                order.append(r["qid"])
    print(f"queue: {len(queue)}  already labeled: {len(done)}")

    entries = rule_entries()
    cats = []
    for r in entries:
        emit = emit_name(r["name"])
        cats.append((slugify(emit), emit, (r.get("body") or "")[:220]))
    cat_slugs = {s for s, _, _ in cats}

    def save() -> None:
        args.out.write_text(
            "\n".join(json.dumps(done[q]) for q in order) + ("\n" if order else ""),
            encoding="utf-8")

    pending = [q for q in queue if q["qid"] not in done]
    start = time.monotonic()
    n_session = 0
    agree = 0
    judged = 0

    print("keys: o=accept prefill  a=allow  b=block  s=skip  u=undo  q=quit  ?=help\n")

    idx = 0
    while idx < len(pending):
        q = pending[idx]
        pre_b, pre_c = q.get("prefill_block"), q.get("prefill_category")
        if pre_c and pre_c not in cat_slugs:
            pre_c = None
        print("=" * WIDTH)
        print(f"[{q['qid']}] {idx+1}/{len(pending)} pending "
              f"| source={q['source']} ref={q.get('ref_id')}")
        render_block("TRANSCRIPT", q["transcript"])
        render_block("ACTION", q["action"])
        print(f"--- ORACLE prefill: "
              f"{'BLOCK' if pre_b else 'allow' if pre_b is False else '?'}"
              f"{f' / {pre_c}' if pre_c else ''}  ({q.get('prefill_note', '')[:90]})")

        key = getkey("verdict [o/a/b/s/u/q/?]: ")
        if key == "?":
            print(__doc__)
            continue
        if key == "q":
            break
        if key == "s":
            idx += 1
            continue
        if key == "u":
            if order:
                undone = order.pop()
                del done[undone]
                save()
                print(f"  undone {undone}")
                pending = [x for x in queue if x["qid"] not in done]
                idx = max(0, len(pending) - (len(queue) - len(done) - 0))
                idx = next((i for i, x in enumerate(pending)
                            if x["qid"] == undone), 0)
            else:
                print("  nothing to undo")
            continue

        block: bool | None = None
        cat: str | None = None
        if key == "o" and pre_b is not None:
            block, cat = pre_b, pre_c
        elif key == "a":
            block, cat = False, None
        elif key == "b":
            block = True
            cat = pick_category(cats, pre_c if pre_b else None)
            if cat is None:
                print("  no category — row left unlabeled")
                idx += 1
                continue
        elif key == "o":
            print("  no prefill on this row — press a or b")
            continue
        else:
            print("  unknown key")
            continue

        if block is False:
            cat = None
        done[q["qid"]] = {"qid": q["qid"], "source": q["source"],
                          "ref_id": q.get("ref_id"),
                          "transcript": q["transcript"], "action": q["action"],
                          "label_block": block, "label_category": cat,
                          "oracle_block": pre_b, "oracle_category": pre_c,
                          "reviewer_ts": time.strftime("%FT%TZ", time.gmtime())}
        order.append(q["qid"])
        save()
        n_session += 1
        if pre_b is not None:
            judged += 1
            agree += (block == pre_b and cat == pre_c)
        idx += 1
        elapsed = time.monotonic() - start
        pace = elapsed / max(1, n_session)
        print(f"  saved {'BLOCK/' + str(cat) if block else 'allow'}  "
              f"[{len(done)}/{len(queue)} done, {pace:.0f}s/row, "
              f"oracle-agree {agree}/{judged}]")

    save()
    print(f"\ndone: {len(done)}/{len(queue)} labeled -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())