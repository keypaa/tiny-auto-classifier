#!/usr/bin/env python3
"""Batch-size profiler: standalone, any CUDA box (Molab, Colab, local).

Same stack as training (bf16 weights, FA2->SDPA chain, LoRA r16, grad
checkpointing, evidenced fused AdamW). Worst-case inputs: longest rows of
a seeded 5,000-row sample (the file is length-ordered; prefix slices lie).
Warmup forward discarded (CUDA autotune contaminates first timing).

Usage:
    python3 scripts/profile_batches.py
    python3 scripts/profile_batches.py --batches 4 8 16 32 --max-len 8192
"""
from __future__ import annotations

import argparse

def profile_batches(base: str = "ProCreations/auto-200m-2",
                    batches: tuple = (1, 2, 4, 8, 16),
                    max_len: int = 8192) -> dict:
    """Batch-size evidence: forward+backward peak mem and throughput.

    Same stack as training (bf16 weights, SDPA, LoRA r16, grad
    checkpointing, 8K rows). Worst case inputs: longest rows of a sample.
    Decides batch size on numbers, not on the OOM traceback alone.
    """
    import time

    import torch
    from datasets import load_dataset
    from peft import LoraConfig, get_peft_model
    from transformers import (AutoModelForSequenceClassification,
                              AutoTokenizer)

    import random as _rng

    assert torch.cuda.is_available()
    ds = load_dataset("ProCreations/auto-1b-data", split="train")
    tok = AutoTokenizer.from_pretrained(base, trust_remote_code=True)
    # Random across the full 712K: the file is length-ordered, so any
    # prefix slice tops out early (first 20K peaked at 2,992 tokens).
    picks = _rng.Random(7).sample(range(len(ds)), 5000)
    lens = [(i, len(tok(ds[i]["text"])["input_ids"])) for i in picks]
    lens.sort(key=lambda t: -t[1])
    worst = [ds[i]["text"] for i, _ in lens[:8]]
    print(f"profiling rows: longest {lens[0][1]} tokens "
          f"(8th longest {lens[7][1]})", flush=True)
    assert lens[0][1] > 6000, \
        f"sample has no 8K rows (max {lens[0][1]}); worst-case claim void"

    # Attention: FA2 via kernels package first, SDPA fallback. Printed so
    # the log shows what actually ran; profiler and training share this.
    model = None
    for attn in ("flash_attention_2", "sdpa"):
        try:
            model = AutoModelForSequenceClassification.from_pretrained(
                base, trust_remote_code=True, num_labels=2,
                dtype=torch.bfloat16,
                attn_implementation=attn).cuda()
            print(f"attention: {attn}", flush=True)
            break
        except Exception as exc:
            print(f"attn {attn} failed:\n{exc}", flush=True)
    assert model is not None
    model = get_peft_model(model, LoraConfig(
        r=16, lora_alpha=16, lora_dropout=0.0, bias="none",
        task_type="SEQ_CLS", target_modules=["Wqkv", "Wi", "Wo"]))
    model.gradient_checkpointing_enable()
    # Optimizer mirrors their recipe: fused AdamW, betas (0.9, 0.95),
    # wd 0.01 (see train_unsloth.py header note for the evidence).
    opt = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=2e-4,
        betas=(0.9, 0.95), weight_decay=0.01, fused=True)
    out = {}
    # Warmup: first forward pays CUDA autotune/caching-allocator init, which
    # otherwise contaminates bs=1 (observed: 4.1s vs 0.7s for bs=2 on the
    # same rows). Discarded, never reported.
    enc = tok(worst[:1], return_tensors="pt", truncation=True,
              max_length=max_len, padding=True).to("cuda")
    labels = torch.zeros(1, dtype=torch.long).cuda()
    with torch.no_grad():
        model(**enc, labels=labels)
    for bs in batches:
        torch.cuda.reset_peak_memory_stats()
        enc = tok(worst[:bs], return_tensors="pt", truncation=True,
                  max_length=max_len, padding=True).to("cuda")
        seqlen = int(enc["input_ids"].shape[1])
        labels = torch.zeros(len(worst[:bs]), dtype=torch.long).cuda()
        t0 = time.monotonic()
        try:
            loss = model(**enc, labels=labels).loss
            loss.backward()
            opt.step()
            opt.zero_grad()
            dt = time.monotonic() - t0
            peak = torch.cuda.max_memory_allocated() / 1e9
            # Computed tokens, not read tokens: rows truncate to seqlen,
            # so counting pre-truncation lengths inflates throughput ~5x
            # (observed: 477K reported vs ~91K computed on 8K rows).
            toks = len(worst[:bs]) * seqlen
            out[str(bs)] = {"ok": True, "step_s": round(dt, 2),
                            "peak_gb": round(peak, 2),
                            "tok_s": int(toks / dt)}
            print(f"  bs={bs}: {dt:.1f}s/step peak={peak:.1f}GB "
                  f"seqlen={seqlen}", flush=True)
        except RuntimeError as exc:
            out[str(bs)] = {"ok": False, "error": str(exc)[:100]}
            print(f"  bs={bs}: FAILED ({str(exc)[:80]})", flush=True)
            torch.cuda.empty_cache()
    return out




def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="ProCreations/auto-200m-2")
    ap.add_argument("--batches", type=int, nargs="+", default=[1, 2, 4, 8, 16])
    ap.add_argument("--max-len", type=int, default=8192)
    args = ap.parse_args()
    import json

    print(json.dumps(profile_batches(args.base, tuple(args.batches),
                                     args.max_len), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
