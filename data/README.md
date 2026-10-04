# Dataset

Pilot `10K`, synthetic, template-based. **Superseded** — see `reports/stage0_findings.md`.

## Files

| Split | Rows | Purpose |
|---|---:|---|
| `train/pilot_10k.jsonl` | 7999 | training |
| `validation/pilot_10k.jsonl` | 1000 | eval during training |
| `test/pilot_10k.jsonl` | 1001 | held-out |
| `adversarial/pilot_10k.jsonl` | 500 | obfuscation / injection |

Regenerate with `python src/dataset/pilot_generator.py --n 10000`.

## Balance (§12)
`20% ALLOW / 20% BLOCK / 20% consent-boundary / 15% minimal-pairs / 10% adversarial / 10% long-context / 5% rule-interaction`
Overall 3259 ALLOW / 6741 BLOCK — BLOCK-heavy by design (safety first).

## Known limitations

- **No rule taxonomy.** `rule_id` is `R17` for every BLOCK row, so roadmap §36 (rule-level precision/recall) is not measurable on this data.
- **Synthetic policy.** `prompts/original/policy.txt` is a placeholder paragraph, so policy-order sensitivity and perturbed-policy tests cannot be run.
- **Template-bound.** 7 categories from ~15 hand-written templates. Plateau is visible in the results.
- **Long-context samples are filler**, not genuinely buried evidence at controlled depths.

## Splits are group-aware
Minimal pairs are kept whole: `src/dataset/pilot_generator.py` groups by `pair_id` before splitting and asserts no pair appears in two splits. An earlier row-level random split put one member of every pair in train and the other in val, which violated §16 and made pair consistency unmeasurable on validation (only 1 pair checkable). Fixed: 750 pair groups, 0 leakage, 81 complete pairs now available in validation.

## Preference pairs
`python src/dataset/dpo_pairs.py` emits `data/train/dpo_pairs.jsonl` — chosen vs subtly-wrong rejected targets, typed by confusion class (consent / rule / hard-soft). Prepared for roadmap §22.

## What replaces this
`ProCreations/auto` (700k, published) covers the same task with a far larger combinatorial space and explicit minimal contrastive pairs. `ProCreations/approve-or-deny` (3k) is a held-out benchmark with published metrics.
