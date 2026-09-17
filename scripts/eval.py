#!/usr/bin/env python3
"""Lean CPU eval — base + LoRA adapter on val/test JSONL, §10 metrics.

Usage:
  python scripts/eval.py --adapter models/checkpoints/pilot_t3_8192 --data data/validation/pilot_10k.jsonl --out reports/eval_pilot_t3_8192.json
  python scripts/eval.py --adapter ... --data ... --limit 50   # smoke subset
  python scripts/eval.py --base ProCreations/auto-0.4b --data ...  # zero-shot (no adapter)

Loads on CPU (inference target), left-truncation via SFTDataset (train/eval parity).
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from transformers import AutoConfig, AutoModelForSequenceClassification, AutoTokenizer

from src.training.dataset import SFTDataset


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default=None, help="LoRA adapter dir (has adapter_config.json)")
    ap.add_argument("--base", default=None, help="Base model id (default: from adapter_config)")
    ap.add_argument("--data", required=True)
    ap.add_argument("--max-length", type=int, default=8192)
    ap.add_argument("--limit", type=int, default=0, help="0 = full file")
    ap.add_argument("--out", default="reports/eval.json")
    args = ap.parse_args()

    base_id = args.base
    if args.adapter:
        cfg = json.loads(Path(args.adapter, "adapter_config.json").read_text())
        base_id = base_id or cfg["base_model_name_or_path"]
        print(f"Adapter {args.adapter}: r={cfg['r']} targets={cfg['target_modules']}")
    assert base_id, "need --base or --adapter"

    tok = AutoTokenizer.from_pretrained(base_id, trust_remote_code=True)
    try:
        tok.truncation_side = "left"
    except Exception:
        pass
    config = AutoConfig.from_pretrained(base_id, trust_remote_code=True)
    config.num_labels = 2
    config.id2label = {0: "approve", 1: "deny"}
    config.label2id = {"approve": 0, "deny": 1}
    print(f"Loading {base_id} on CPU...")
    model = AutoModelForSequenceClassification.from_pretrained(
        base_id, config=config, trust_remote_code=True, torch_dtype=torch.float32)
    if args.adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, args.adapter)
        print("Adapter merged for eval.")
    model.eval()

    ds = SFTDataset(args.data, tok, "prompts/original/policy.txt",
                    mode="encoder", max_length=args.max_length, verify_policy=True)
    n = len(ds) if not args.limit else min(args.limit, len(ds))
    print(f"Evaluating {n}/{len(ds)} samples at max_length={args.max_length}...")

    correct = fa_num = fa_den = fd_num = fd_den = 0
    lat = []
    preds = []
    with torch.no_grad():
        for i in range(n):
            b = ds[i]
            t0 = time.perf_counter()
            logits = model(input_ids=b["input_ids"].unsqueeze(0),
                           attention_mask=b["attention_mask"].unsqueeze(0)).logits
            lat.append((time.perf_counter() - t0) * 1000)
            pred = int(logits.argmax(-1))
            true = int(b["labels"])
            correct += pred == true
            if true == 1:
                fa_den += 1; fa_num += pred == 0
            else:
                fd_den += 1; fd_num += pred == 1
            if i < 5 or (i + 1) % 50 == 0:
                print(f"  {i+1}/{n} acc={correct/(i+1):.3f} p50={sorted(lat)[len(lat)//2]:.0f}ms", flush=True)
            preds.append({"i": i, "pred": pred, "true": true})
            if i == 0:
                print(f"  sample0 tokens={len(b['input_ids'])} truncated={bool(b['truncated'])}")

    import numpy as np
    lat = np.array(lat)
    metrics = {
        "adapter": args.adapter, "base": base_id, "data": args.data,
        "n": n, "max_length": args.max_length,
        "accuracy": round(correct / n, 4),
        "false_allow": round(fa_num / max(1, fa_den), 4),
        "false_deny": round(fd_num / max(1, fd_den), 4),
        "latency_p50_ms": round(float(np.percentile(lat, 50)), 1),
        "latency_p95_ms": round(float(np.percentile(lat, 95)), 1),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(metrics, indent=2))
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
