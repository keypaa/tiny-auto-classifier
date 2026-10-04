# Task: design a plan for a tiny CPU-only policy-enforcement classifier

You are given a research/engineering problem. Produce a **concrete, staged plan** — not a survey of options. Commit to recommendations, justify them with numbers, and say explicitly where you disagree with our current direction.

---

## 1. The mission

Build the smallest CPU-only model that can apply an **unchanged** safety policy to agent action requests, and decide `ALLOW` or `BLOCK + exact rule id`, falling back to an expensive classifier when it is not confident.

This is **not** a chat model. It is a **decision model** that reads a large fixed policy plus a short dynamic task description.

Priority order (safety first, do not reorder):
1. dangerous false-allow rate
2. hard-block recall
3. long-context robustness
4. rule accuracy
5. false-deny rate
6. CPU latency (p50/p95)
7. RAM
8. disk size

A false-deny costs a human confirmation. A false-allow lets a destructive or exfiltrating action execute unattended. The asymmetry is at least 10:1.

## 2. Hard constraints

**Inference (this is the product):**
- CPU only. No GPU at inference, ever. Target: 6-core AMD Ryzen 4500U, AVX2 (no AVX-512), 21 GB DDR4.
- Batch size 1. Low latency. p95 matters more than p50.
- Must run with no network access and no per-call cost.

**Input:**
- A fixed policy of ~108,000 characters, roughly 27,000 tokens (measured 21,627 with one BPE vocab, 27,110 with another).
- The policy is byte-identical on every request. **It must not be summarized, compressed, retrieved piecewise, or rewritten.** Hard constraint for the primary track.
- Plus a short dynamic suffix (transcript excerpt + proposed action + metadata): median 46 tokens, occasionally up to ~25,000 tokens for long-transcript cases.
- Total ~27K tokens, which dominates cost.

**Output:**
- Deterministic and structured (JSON or fixed schema). No free text.
- Must emit a specific rule id, not just a boolean.
- Any malformed / unknown-rule / context-overflow case must fail closed. Never silently allow.

## 3. Current state of knowledge — please do not redo this work

All numbers below are measured, not estimated.

### Candidate models (context lengths verified)

| Model | Params | Architecture | Context | Notes |
|---|---|---|---|---|
| `ProCreations/auto-0.4b` | 395.8M | ModernBERT-large encoder, `SequenceClassification`, 2 labels (`approve`/`deny`) | 8K native, **64K** after RoPE extension (global theta 2.56M, local 10000) | Only known open checkpoint that is a *classifier* with verified 64K |
| `convaiinnovations/laya` and `-typed-decisions` | 421M | ModernBERT-large + decision head trained from scratch: 2 transformer layers, per-option `[MASK]` marker scorer, act/escalate head. Apache 2.0 | 512 / 1024 (multilingual variant 8192) | Trained with **RLCD** (REINFORCE/GRPO against strictly proper scoring rules). Ships over-confident: ECE 0.46, drops to 0.08 after temperature refit |
| `Jev` (TypeSafe AI) | unknown | proprietary decision-only service | 32K | **No public weights.** Hosted API at $0.042 per 1M tokens. Not usable as a base for a local model. Validates the architecture thesis only |
| `LiquidAI/LFM2.5-230M` | 230M | hybrid: 8 double-gated conv + 6 GQA blocks, causal LM | 32K | fast on CPU |
| `google/gemma-3-270m` | 270M | decoder, 5 local : 1 global attention, sliding window 1024, RoPE base 1M global | 32K | 170M of the 270M is embedding |
| `Qwen/Qwen3-0.6B` | 600M | causal LM, GQA 16/8 | 32K native, up to 128K with YaRN + DCA | quality ceiling, 2x the FLOPs of auto-0.4b |

### Measured results

**Our T3 pilot** (`auto-0.4b`, LoRA r16 on Wqkv/Wi/Wo, 7.2M trainable, left-truncated 8K, 8000 train / 1000 val, 1 epoch, 74 min on an RTX PRO 6000):
- `0.876` accuracy, `0.0336` false-allow, `0.2948` false-deny, frozen from epoch 0.2 onward.
- Val set is 65.4% BLOCK, so 0.876 is genuinely above the majority baseline.
- Training loss fell 14.9 to 0.98 while eval loss oscillated 0.26 to 0.34, i.e. mild overfit onset.
- We believe the residual 12.4% error is **not** learnable at 8K because long-context samples bury the decisive evidence in ~108 KB of filler, and left-truncation at 8K keeps only the last ~32 KB.

**Laya zero-shot transfer probe** (`laya-typed-decisions`, 100 val samples, choice question allow/block, state = full 27K prompt):
- `0.74` accuracy, `0.0` false-allow, `1.0` false-deny, p50 122 ms on T4.
- `answer_confidence` was **exactly 0.594 on all 100 samples** (min = max = median). Degenerate always-block collapse with no usable signal.
- Verdict: their *weights* are task-specific and do not transfer. Their *recipe* (RLCD, option-marker head, act/escalate head, calibration tooling) may still be the best available design reference.

**CPU inference latency** (`auto-0.4b`, 8K tokens, fp32, single forward, Ryzen 4500U):
- p50 ~102 s, p95 ~113 s. Full 1000-sample val eval on CPU would take ~28 hours.
- This is currently the binding deployment constraint, and it is why we have not yet measured end-to-end CPU latency for a real candidate.

**Cost model on T4 (65 TFLOPS fp16 peak, ~19.5 effective at 30% util):**
- FLOPs per sample at L tokens for a 395M model with sparse attention: `8*N*L + 3*Attn`.
- `8K` = 30.2 TFLOP/sample, `27K` = 131.4 TFLOP/sample (base 85.8 + attention 45.5).
- 8000 samples at 8K = ~4.3 h; at 27K = ~18.7 h.

### Traps we hit (so you don't repeat them)

1. **Truncation side is catastrophic and silent.** With `truncation=True` (right side), the 27K-token policy prefix is kept and the transcript + action are cut off entirely. Every training sample became an identical policy prefix, the model learned only the prior, and eval froze at the majority rate while training loss decreased normally. Must left-truncate, and must verify that the action tokens survive into the tail.
2. **bf16 is emulated on Turing (T4) and is 1.7x slower than fp32.** Measured 350 ms/chunk bf16 vs 202 ms fp32. fp16 has native tensor cores; bf16 does not. On Blackwell / A100 / H100 bf16 is native and is the right choice.
3. **torch 2.11 + accelerate 1.5 fp16 GradScaler bug**: `_get_grad_norm` calls `clip_grad_norm_(inf)` even with `max_grad_norm=0`, which triggers `ValueError: Attempting to unscale FP16 gradients`. Use bf16 on capable GPUs.
4. **Bag-of-chunks embeddings of a constant policy are pure waste.** The policy is identical across rows, so a frozen-embedder + pooling baseline contributes an identical vector to every sample and zero discriminative signal. Measured: policy is 21,627 tokens, dynamic suffix median 46 tokens.
5. LoRA adapters are ~29 MB for 7.2M params on a 395M model; full-weight checkpoints are ~1.6 GB and OOM small GPUs.

### What we have NOT done

- No curriculum step beyond 8K has completed (16K and 27K runs were lost to infrastructure failures, not to the method).
- **No real policy data.** Our 10,000-example pilot set is synthetic and template-based, built from ~15 hand-written templates across 7 categories. This is very likely the weakest link in the whole project.
- No teacher labeling, no multi-head rule+severity head, no DPO, no verifier RL, no quantization, no ONNX export, no calibration work, no cascade implementation.
- No T0 floor baseline yet (running now).
- No adversarial evaluation set, no position-robustness evaluation, no rule-level confusion matrix.

## 4. Open questions we want your opinion on

1. **Is 27K context actually necessary?** The policy is a fixed prefix. Alternatives that preserve the "unchanged policy" requirement: prefix KV-cache reuse (decoder only), caching a distilled representation of the policy (which we read as violating the constraint but you may argue otherwise), or training at 27K directly. What is your recommendation and how would you validate it?
2. **Encoder vs decoder at this size.** We assume an encoder wins because there is no generation waste. Convince us otherwise if you can.
3. **Multi-head from the start?** Current head is 2-class. We need rule id and hard/soft severity. Laya's option-marker scoring with a `[MASK]` per rule looks like a clean way to get rule id without a 30-way softmax. Worth copying?
4. **Distillation.** If we settle on a 395M teacher that works, is target distillation into a ~150M or smaller encoder the right final move, given that CPU latency is currently the binding constraint?
5. **Quantization.** INT8 and INT4 on CPU. Which layers must stay in higher precision for a safety-critical classifier? Has anyone measured safety degradation from quantization on a long-context encoder?
6. **Dataset.** Concretely: how would you build 50K to 200K high-quality examples that encode policy semantics, minimal pairs that flip exactly one property, hard negatives, and adversarial obfuscation, without a strong teacher model? Our synthetic templates clearly plateau.
7. **Is the 12.4% residual error at 8K a length problem or a data problem?** How would you distinguish these two cheaply?

## 5. What to return

- A staged plan with explicit gates and go/no-go criteria between stages.
- For each stage: what is built, what is measured, and what result would justify continuing versus pivoting.
- Your recommended model class, parameter count, context length, and training recipe, with reasoning tied to the measured numbers above.
- Your recommended evaluation protocol, especially how to measure CPU p95 latency and false-allow rate cheaply enough to iterate.
- An explicit list of places where you think our current direction is wrong, with the argument.
- Flag any assumption in section 3 that you believe is wrong or stale.
