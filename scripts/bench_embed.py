#!/usr/bin/env python3
"""Micro-bench: why is chunked embedding slow? 30s, facts not guesses.

Times ONE batch forward under the exact conditions t0_baseline uses, and
isolates the suspects: device, dtype, attention_mask, attn_implementation.
"""
from __future__ import annotations
import time, sys
import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

MODEL = "nomic-ai/modernbert-embed-base"
dev = "cuda" if torch.cuda.is_available() else "cpu"
print(f"device={dev} torch={torch.__version__} bf16_supported={torch.cuda.is_bf16_supported() if dev=='cuda' else False}")
print(f"attn impl available: {hasattr(torch.nn.functional, 'scaled_dot_product_attention')}")

tok = AutoTokenizer.from_pretrained(MODEL)
model = AutoModel.from_pretrained(MODEL).to(dev).eval()
print("attn_implementation in config:", getattr(model.config, "_attn_implementation", "?"))
print("params:", sum(p.numel() for p in model.parameters()) / 1e6, "M | layers:", model.config.num_hidden_layers,
      "| hidden:", model.config.hidden_size)


def bench(bs, L, dtype, use_mask, iters=5):
    if dtype == torch.bfloat16:
        model.to(torch.bfloat16)
    else:
        model.float()
    ids = torch.randint(0, 50000, (bs, L), device=dev)
    kw = {"attention_mask": torch.ones(bs, L, dtype=torch.long, device=dev)} if use_mask else {}
    with torch.no_grad():
        model(**kw, input_ids=ids)  # warmup
        if dev == "cuda":
            torch.cuda.synchronize()
        t0 = time.perf_counter()
        for _ in range(iters):
            model(**kw, input_ids=ids)
        if dev == "cuda":
            torch.cuda.synchronize()
    dt = (time.perf_counter() - t0) / iters
    return dt, dt / bs  # total, per-chunk


print(f"\n{'bs':>4} {'len':>6} {'dtype':>9} {'mask':>5} {'sec/batch':>11} {'ms/chunk':>10} {'chunks/s':>10}")
for dtype in ([torch.float32, torch.bfloat16] if dev == "cuda" else [torch.float32]):
    for bs, L in [(1, 1024), (16, 1024), (64, 1024), (64, 512)]:
        for use_mask in [True, False]:
            dt, per = bench(bs, L, dtype, use_mask)
            print(f"{bs:>4} {L:>6} {str(dtype).replace('torch.',''):>9} {str(use_mask):>5} {dt:>11.3f} {per*1000:>10.1f} {1/per:>10.1f}")

print("\n--- same, but token_type_ids passed too (ModernBERT may want them) ---")
for bs, L in [(64, 1024)]:
    dt, per = bench(bs, L, torch.bfloat16, True)
    print(f"baseline bs={bs} L={L} bf16 mask: {per*1000:.1f} ms/chunk -> {1/per:.0f} chunks/s")
    ids = torch.randint(0, 50000, (bs, L), device=dev)
    with torch.no_grad():
        model(input_ids=ids, attention_mask=torch.ones(bs, L, dtype=torch.long, device=dev),
              token_type_ids=torch.zeros(bs, L, dtype=torch.long, device=dev))
        if dev == "cuda":
            torch.cuda.synchronize()
        t0 = time.perf_counter()
        for _ in range(5):
            model(input_ids=ids, attention_mask=torch.ones(bs, L, dtype=torch.long, device=dev),
                  token_type_ids=torch.zeros(bs, L, dtype=torch.long, device=dev))
        if dev == "cuda":
            torch.cuda.synchronize()
    dt = (time.perf_counter() - t0) / 5
    print(f"with token_type_ids: {dt/bs*1000:.1f} ms/chunk -> {bs/dt:.0f} chunks/s")

if dev == "cuda":
    print(f"\npeak VRAM during bench: {torch.cuda.max_memory_allocated()/1e9:.2f} GB")
n = 201624
for rate in [6, 50, 200, 800, 2000]:
    print(f"  at {rate:>5} chunks/s -> {n/rate/60:>6.1f} min for {n} chunks")
