# Stage 0 Findings — RETRACTED, the ablation was invalid

> **Status: retracted 2026-10-05.** The original heading read "the in-context
> hypothesis failed its own test", and §1 concluded the model "is not applying the
> policy in-context". **That conclusion is wrong and is withdrawn.**
>
> The ablation below is measured correctly but tests nothing, because the policy it
> used was a placeholder. See §0. The numbers are kept for reference. The
> interpretation is not.
>
> A valid ablation is now possible: the real 140,411-char rulebook is in hand
> (`stage2_verdict_prompt.txt`, resolved via `Yqr` — see `analysis/Q2-slot-resolution.md`).
> It has 73 distinct named BLOCK rules, so retrieval has 73 real targets instead of one.

*Model: `ProCreations/auto-0.4b` (ModernBERT-large 395M) + our LoRA r16 adapter `pilot_t3_8192`.
Eval: 300 samples of `data/validation/pilot_10k.jsonl`, `max_length=8192`, left-truncated, GPU fp16.
Raw: `reports/stage0_diag.json` (regenerate with `scripts/diag_stage0.py`).*

Baseline for reference: the training run reported `0.876 / 0.0336 / 0.2948` on the full 1000-sample val.

---

## 0. Why the original conclusion was wrong

The placeholder policy was **one 611-character rule repeated 177 times** (108,313 chars total). It contained exactly **one** distinct rule.

A retrieval test needs distinct targets. With one target copied 177 times:

- A model can score well by pattern-matching that single string **anywhere** in the input. It never needs to locate the *correct* rule.
- Removing the policy removes no information the action and transcript did not already carry.

So `none ≈ full` measured the placeholder, not the architecture. The in-context question is **unanswered**, not answered.

The same defect invalidated `compact`: cutting a single repeated paragraph at 8,000 chars slices mid-repeat into text that is textually identical to what it cut. The 0.40 always-ALLOW collapse is an artifact of that cut, not a measurement of how much policy is required.

**What survives from the original report:** §2 (per-category error breakdown), §3 (the two unmeasurable diagnostics and the pair-split fix), and §4's observation that our numbers and ProCreations' are not comparable. Those do not depend on the policy content.

**What does not survive:** §1's claim that the model is a learned-rule model, and §4's conclusion 1 built on it.

---

## 1. Policy ablation — measured, but uninterpretable

Same model, same samples, three policy conditions. All three conditions used the placeholder policy, so none of them is a valid in-context test.

| Condition | Policy chars | Prompt tokens | Accuracy | False-allow | False-deny |
|---|---:|---:|---:|---:|---:|
| `full` | 108,313 | 8192 | 0.8833 | 0.0294 | 0.3021 |
| `compact` | 8,000 | 1643 | 0.4000 | 0.8824 | 0.0000 |
| `none` | **0** | 45 | 0.8733 | 0.0833 | 0.2188 |

Read these as *three datasets, not three policy conditions*. The gap between `full` and `none` is a 1-point accuracy difference over 300 samples on a task where one repeated rule carried all the signal.

**To redo this properly:** ablate the real 73-rule rulebook. Useful conditions are (a) full rulebook, (b) rulebook with the matched rule removed, (c) rulebook with all rules replaced by a single generic rule, (d) no rulebook. Condition (b) is the one that actually tests retrieval — if accuracy holds when the matching rule is deleted, the model was never using it.

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

> The comparison table below stands. The two conclusions that followed from it do not, because one of them rested on the retracted §1. See §0.

| | Our adapter | `ProCreations/auto-0.4b` | `ProCreations/auto-1b` |
|---|---|---|---|
| Accuracy | 0.883 (our synthetic data) | 0.9013 (their benchmark) | **0.9640** |
| False-approve | 0.0294 | 0.0871 | — |
| False-deny | 0.3021 | 0.1088 | **0.0319** |
| Long-context (16k–64k) | untested | 0.7845 | **0.9702** |
| Policy in input | yes (**applicability untested**) | **no** | **no** |

Our numbers and theirs are **not comparable** — different datasets. That is the central blocker for every strategic decision: we have been choosing an architecture on non-comparable numbers.

Still established:
1. ~~Our current model is a learned-rule model that does not read the policy.~~ **Withdrawn.** The ablation that supported this was invalid (§0). Whether our adapter reads a real policy is an open question.
2. Whether a learned-rule model is *acceptable* is a product decision, not a modelling one. The policy has been stable across ~20 Claude Code versions, which weakens the "it will change" argument, but the retrain loop is real.

---

## 5. Next measurements

**Redo the ablation against the real rulebook** (§0). Condition (b), rulebook-minus-matched-rule, is the one that decides whether in-context application is real. This is now possible and was not before.

**Establish a comparable benchmark.** Evaluate our adapter, `auto-0.4b` and `auto-1b` on the **same** held-out set — `ProCreations/approve-or-deny` (3,000 items, published) — and measure int8 latency on the target Ryzen. That yields the first apples-to-apples comparison and the first real CPU latency figure for the learned-rule path.

**Decide learned-rule versus in-context** only after the first of these lands.
