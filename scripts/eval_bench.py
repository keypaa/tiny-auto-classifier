#!/usr/bin/env python3
"""Evaluate a classifier on ProCreations/approve-or-deny.

Same columns as their leaderboard: accuracy, balanced acc, false-approve
(deny waved through — the metric that matters), false-deny, latency/call.
Scored at 0.5, deny is the positive class, matching their protocol.

Usage:
    python3 scripts/eval_bench.py --n 100
    python3 scripts/eval_bench.py --n 3000 --out reports/bench_full.json
    python3 scripts/eval_bench.py --model ProCreations/auto-1b --n 100
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent.parent


def load_bench():
    from datasets import load_from_disk

    return load_from_disk(str(ROOT / "data" / "benchmark" / "approve-or-deny"))


def strat_sample(ds, n: int, seed: int):
    import random

    if n >= len(ds):
        return list(range(len(ds)))
    rng = random.Random(seed)
    buckets: dict[tuple, list[int]] = {}
    for i, r in enumerate(ds):
        buckets.setdefault((r["label"], r["difficulty"]), []).append(i)
    per, rem = divmod(n, len(buckets))
    idx: list[int] = []
    for k in sorted(buckets):
        idx.extend(rng.sample(buckets[k], min(len(buckets[k]), per + (1 if rem > 0 else 0))))
        rem -= 1
    return sorted(idx)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="ProCreations/auto-0.4b")
    ap.add_argument("--adapter", default=str(ROOT / "models" / "checkpoints" / "pilot_t3_8192"))
    ap.add_argument("--no-adapter", action="store_true")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--max-length", type=int, default=8192)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    ds = load_bench()
    idx = strat_sample(ds, args.n, args.seed)
    print(f"bench: {len(ds)} rows, evaluating {len(idx)} (seed {args.seed})")

    tok = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.model, trust_remote_code=True, dtype=torch.float32)
    tag = args.model
    if args.adapter and not args.no_adapter:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, args.adapter)
        tag += f"+{Path(args.adapter).name}"
    model.eval()
    id2label = {int(k): v for k, v in
                model.config.id2label.items() if isinstance(model.config.id2label, dict)}
    if not id2label and hasattr(model, "base_model"):
        id2label = {int(k): v for k, v in model.base_model.config.id2label.items()}
    print(f"model: {tag}  id2label={id2label}")

    y_true, y_pred, cats, lat = [], [], [], []
    t_all = time.monotonic()
    for j, i in enumerate(idx):
        r = ds[int(i)]
        enc = tok(r["text"], return_tensors="pt", truncation=True,
                  max_length=args.max_length)
        t0 = time.monotonic()
        with torch.no_grad():
            logits = model(**enc).logits[0].tolist()
        lat.append(time.monotonic() - t0)
        pred = id2label[int(max(range(len(logits)), key=logits.__getitem__))]
        y_true.append(r["label"])
        y_pred.append(pred)
        cats.append(r["category"])
        if (j + 1) % 25 == 0:
            print(f"  {j+1}/{len(idx)} ({(time.monotonic()-t_all)/(j+1):.1f}s/row)",
                  flush=True)

    tp = sum(1 for t, p in zip(y_true, y_pred) if t == "deny" and p == "deny")
    tn = sum(1 for t, p in zip(y_true, y_pred) if t == "approve" and p == "approve")
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == "deny" and p == "approve")
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == "approve" and p == "deny")
    n = len(idx)
    res = {
        "model": tag,
        "n": n,
        "accuracy": round((tp + tn) / n, 4),
        "balanced_acc": round(((tp / (tp + fn)) + (tn / (tn + fp))) / 2, 4) if (tp + fn) and (tn + fp) else None,
        "false_approve": round(fp / (tp + fp), 4) if (tp + fp) else None,
        "false_deny": round(fn / (tn + fn), 4) if (tn + fn) else None,
        "latency_s": round(sum(lat) / len(lat), 3),
        "confusion": {"tp_deny": tp, "tn_approve": tn, "fp_waved": fp, "fn_blocked": fn},
    }
    print(f"\n{tag}  n={n}")
    print(f"  accuracy={res['accuracy']}  balanced={res['balanced_acc']}")
    print(f"  FALSE-APPROVE={res['false_approve']}  false-deny={res['false_deny']}")
    print(f"  latency={res['latency_s']}s/call")
    if args.out:
        args.out.write_text(json.dumps(res, indent=2))
        print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())