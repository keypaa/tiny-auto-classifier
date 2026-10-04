# Stage 0 Findings — the in-context hypothesis failed its own test

*Model: `ProCreations/auto-0.4b` (ModernBERT-large 395M) + our LoRA r16 adapter `pilot_t3_8192`.
Eval: 300 samples of `data/validation/pilot_10k.jsonl`, `max_length=8192`, left-truncated, GPU fp16.
Raw: `reports/stage0_diag.json` (regenerate with `scripts/diag_stage0.py`).*

Baseline for reference: the training run reported `0.876 / 0.0336 / 0.2948` on the full 1000-sample val.

---

## 1. Policy ablation — the headline result

Same model, same samples, three policy conditions.

| Condition | Policy chars | Prompt tokens | Accuracy | False-allow | False-deny |
|---|---:|---:|---:|---:|---:|
| `full` | 108,313 | 8192 | **0.8833** | 0.0294 | 0.3021 |
| `compact` | 8,000 | 1643 | 0.4000 | 0.8824 | 0.0000 |
| `none` | **0** | 45 | **0.8733** | 0.0833 | 0.2188 |

**`none` is within 1 accuracy point of `full`.** The model reaches 87.3% with **no policy in the input at all**. It is not applying the policy in-context; it learned to classify from the action and transcript alone.

**Consequence:** our fine-tuned model is a *learned-rule* model — the same category as `ProCreations/auto-0.4b` and `auto-1b`, whose input format also contains no policy. The roadmap §2 constraint ("the policy must remain intact") currently buys us nothing.

### `compact` is an artifact, not a finding

Our synthetic policy is a single repeated paragraph, so cutting at 8,000 characters slices mid-paragraph into text the model never saw. The 0.40 always-ALLOW collapse is out-of-distribution behaviour and says nothing about how much policy is required. A valid compact test needs the real policy and clean paragraph boundaries.

---

## 2. Where the errors are

| Category | n | Accuracy | False-allow | False-deny |
|---|---:|---:|---:|---:|
| `clear_block` | 58 | 1.000 | 0.000 | 0.000 |
| `consent_boundary` | 68 | **1.000** | 0.000 | 0.000 |
| `adversarial` | 30 | 1.000 | 0.000 | 0.000 |
| `long_context` | 24 | 1.000 | 0.000 | 0.000 |
| `rule_interaction` | 14 | 0.571 | **0.4286** | 0.000 |
| `clear_allow` | 58 | 0.741 | 0.000 | **0.2586** |
| `minimal_pairs` | 48 | 0.708 | 0.000 | **0.6364** |

- **All 6 dangerous false-allows sit in `rule_interaction`** — the scope-escalation cases (`local → shared` namespace, `read → upload`). It is the only category with FA > 0. n=14 is small, but it is the direction that matters for safety.
- **Consent reasoning learned perfectly** (1.000 on n=68, zero FA, zero FD). This was the capability we were most worried about losing, and it is intact.
- **All remaining error mass is over-blocking**: 15 errors in `clear_allow`, 14 in `minimal_pairs`.

---

## 3. Two diagnostics that returned nothing, and why

**Suffix-length stratification is void by construction.** Every sample tokenises to exactly `max_length` because the policy dominates; `p25 = p50 = p75 = p90 = p99 = 8192`. There is no variation to stratify on. To make this diagnostic meaningful, the policy must be excluded from the tokenized input for length purposes (or the model must run at several `max_length` values over the same data).

**Pair consistency checked 1 pair.** The split was random at row level, so minimal-pair members landed in different splits. This violated roadmap §16 ("hold out entire pairs") and made the metric structurally unmeasurable. **Fixed** in `src/dataset/pilot_generator.py`: splits are now grouped by `pair_id`, with a hard assertion that no pair appears in two splits. Regenerated data has 750 pair groups, 0 leakage, and 81 complete pairs available in validation (was 1).

---

## 4. What this means

| | Our adapter | `ProCreations/auto-0.4b` | `ProCreations/auto-1b` |
|---|---|---|---|
| Accuracy | 0.883 (our synthetic data) | 0.9013 (their benchmark) | **0.9640** |
| False-approve | 0.0294 | 0.0871 | — |
| False-deny | 0.3021 | 0.1088 | **0.0319** |
| Long-context (16k–64k) | untested | 0.7845 | **0.9702** |
| Policy in input | yes (ignored) | **no** | **no** |

Our numbers and theirs are **not comparable** — different datasets. That is the central blocker for every strategic decision: we have been choosing an architecture on non-comparable numbers.

Two things are now established:
1. Our current model is a learned-rule model that does not read the policy. If in-context application is the goal, the current recipe does not achieve it.
2. Whether a learned-rule model is *acceptable* is a product decision, not a modelling one: the policy has been stable across ~20 Claude Code versions, which weakens the "it will change" argument, but the retrain loop is real.

---

## 5. Next measurement that unblocks the decision

Evaluate our adapter, `auto-0.4b` and `auto-1b` on the **same** held-out benchmark — `ProCreations/approve-or-deny` (3,000 items, 91 columns, published) — and measure `auto-1b-ONNX` int8 latency on the target Ryzen. That yields the first apples-to-apples accuracy comparison and the first real CPU latency figure for the learned-rule path.
