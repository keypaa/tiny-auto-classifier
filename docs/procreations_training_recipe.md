# ProCreations training recipe — evidence log

*Sources: `ProCreations/auto-200m-2` repo files (`training/train.py`,
`training/plan.json`), `ProCreations/auto-0.4b-2/environment.json`,
`ProCreations/auto-1b/training_metadata.json`. Fetched 2026-10-11. Pins:
data `d265bbf7`, bench `a38b6259`, 200m-2 init `answerdotai/ModernBERT-base
@8949b909`. Purpose: every training choice below is traceable, so nobody
re-derives it. Where we deviate, the deviation is marked as ours.*

## Optimizer

Fused AdamW, FP32 master weights, BF16 autocast, betas (0.9, 0.95), weight
decay 0.01, grad clip 1.0. 3% warmup, cosine decay to 10% of peak.
(Source: `plan.json` experiment record.)

Ours: same optimizer and schedule via `adamw_torch_fused`, `warmup_ratio`
0.03, cosine, `max_grad_norm` 1.0. Deviation: base weights load BF16, not
FP32 master (22 GB vs their 96 GB iron).

## Loss

Distillation: `(1-alpha)*CE(label) + alpha*T^2*KL(teacher/T || student/T)`,
alpha 0.5, temperature 2.0. Teacher = cached Auto 3B logits over train and
augmented rows; augmented rows use the teacher argmax as label.
(Source: `plan.json` loss field.)

Ours: plain CE for now. Distillation is a documented upgrade requiring a
teacher-logits generation pass (auto-1b over 712K rows) we have not run.
`deny_weight` exists in their engine (default 1.0) — the lever if our
false-deny stays high; not wired yet.

## Batching

Length-bucketed microbatches up to 65,536 padded tokens / 128 rows; optimizer
step at ≥128 rows or ≥131,072 tokens. Every row at full length (no
truncation). (Source: `plan.json` batching field.)

Ours: token-budget bucketing (same idea, 32–64K budgets) plus HF
`group_by_length` where applicable. Truncation at 8K is our deviation,
forced by L4 VRAM — stated, not hidden.

## Schedule

LR pilots first: 120K rows × 1 epoch over [3e-5, 6e-5, 1e-4], pick lowest
validation NLL. Then p1_short (3 epochs at pilot LR) → p2_long (2 epochs at
0.4× LR) with short replay 1.0 + augmented replay 0.25. (Source:
`train.py pilots()` + `plan.json` runs.)

Ours: LoRA r16, so their full-param LR range does not transfer directly.
Sweep by repeated invocation (not sweep-mode in code), starting around
[5e-5, 1e-4, 2e-4]. Criterion stays theirs: lowest validation NLL.

## Precision and attention

FP32 master, BF16 autocast. Attention via
`kernels-community/flash-attn2@81fb77c` with `kernels` 0.16.2.
(Source: `environment.json` both models.)
Device: RTX PRO 6000 Blackwell 96 GB. Stack: torch 2.13+cu130,
transformers 5.16.1, CUDA 13.

Ours: BF16 weights + BF16 compute (L4 supports both; T4 lacks BF16).
FA2 attempted via `kernels==0.16.2` (their pinned version — unpinned
resolves to 0.17.2, which breaks transformers' hub_kernels integration at
import; that failure was observed, not hypothesized) with automatic
fallback to SDPA. Routing verified in transformers v5.16.1 source
(`modeling_modernbert.py`): `_supports_flash_attn = True`, and the
attention interface receives `sliding_window` explicitly — there is no
architectural block on FA2 + sliding window, only version skew. Do not
"go back" to PyPI flash-attn: its build needs `packaging` at metadata
time and fails the whole image.

## Init and selection

Init: ModernBERT-base, CLS pooling, global-attention RoPE theta by MLM
sweep, 65,536 positions. Selection: lowest validation NLL over candidates
including weight averages — never accuracy, never the benchmark. Audit
partition and bench evaluated once, on the frozen candidate only.
(Source: `plan.json` initialization/selection; `training_metadata.json`
best_event: long stage, NLL-selected, 97.82% acc / 2.44% FA / 1.92% FD.)

Ours: same selection rule (NLL on val selection split). Bench stays blind
until the candidate freezes — their amendment history shows why (p2 raised
val NLL while looking fine; the rule caught it).

## Data

`auto-1b-data @d265bbf7`, 711,985 rows / 516.7M tokens, plus 120K archived
cross-paired rows labelled only by the teacher. Validation partitions
shared with 0.4b-2. Bench never used for training, pilots, or selection.
(Source: `plan.json` data field.)

## Batch evidence (RTX PRO 6000 96 GB, measured 2026-10-11)

Profiler: `scripts/profile_batches.py` — 8 longest rows of a seeded 5,000-row
sample (39–48K tokens, truncated to 8K), LoRA r16, fused AdamW (0.9, 0.95),
wd 0.01, grad checkpointing, BF16. Warmup forward discarded.

| Batch | Step | Peak mem | Throughput |
|---|---|---|---|
| 4 | 0.55 s | 24.9 GB | 331K tok/s |
| 8 | 0.72 s | 49.6 GB | 477K tok/s |
| 16 | 0.72 s | 49.7 GB | 477K tok/s |
| 32 | 0.72 s | 49.7 GB | 477K tok/s |
| 64 | 1.11 s | 49.5 GB | 309K tok/s |

Readings:

- **Batch 8.** Throughput plateaus at 8 (16/32 add nothing), memory plateaus
  at ~50 GB from 8 up, and 64 is *slower* than 8 (309K vs 477K tok/s) —
  bigger is not monotonically faster. 8 maximizes throughput per GB with
  46 GB headroom.
- **Peak flatlines, not scales.** 49.6 → 49.7 GB across 8/16/32/64 means the
  ceiling is fixed workspace (FA2 buffers, allocator pools), not per-row
  activations at these shapes. Do not extrapolate this flatness to longer
  contexts — re-profile for 64K training.
- **FA2 confirmed via kernels.** Transformers log line, verbatim:
  `You do not have flash_attn installed, using kernels-community/flash-attn2
  from the kernels library instead!` plus `attention: flash_attention_2`
  on every run. Requires `kernels==0.16.2` (their pin); 0.17.2 breaks the
  hub_kernels integration at import (observed).
- L4 numbers (batch 2 max, 12.7 GB peak, OOM at 4) are a different iron
  class and do not transfer. Re-profile per card, always.

Derived (not yet confirmed): batch 8 × grad-accum 16 = 128 rows/update,
mirroring their ≥128-row step scale. Grad-accum changes dynamics, not
memory — confirm before the full run.

## Smoke budget (measured inputs, 2026-10-11)

50K-row subset (seed 7): 36.94M raw tokens, **28.91M computed** (capped at
8K; 729 rows truncated, 1.5%). Median 375, p90 659, p99 11,820 tokens.
At batch 8: 6,250 microsteps, 391 updates at accum 16.

Wall time model (6000, FA2, anchor 0.72 s per 65K-token microstep):
stepping 8–75 min by microstep cost, mid ~16 min, plus ~5 min fixed
(tokenize, load, 4 evals on 1K rows). Realistic total **15–25 min**.
Grad-accum changes update count only (391 vs 6,250), never wall time.

## What this file does not cover

Distillation teacher logits (not generated), RoPE theta sweep values
(recorded as "by MLM sweep" — the actual theta lives in their config, not
here), exact per-GPU batch layout on their 96 GB card (irrelevant to ours).
