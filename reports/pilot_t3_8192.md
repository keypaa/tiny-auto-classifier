# Pilot T3 8K — Run Report (from `train.log`, not hand-copied)

*Machine: RTX PRO 6000 Blackwell 98GB, bf16 (auto-upgraded from fp16), LoRA r16 (7.1M / 1.79%), batch 1×8, 1 epoch. Data: `reports/pilot_t3_8192.json`.*

## Result

| Metric | Value |
|---|---|
| Steps | 1000/1000 (74min, 3.9s/it) |
| Train loss | 14.92 → 0.98 (final avg 2.77) |
| Eval accuracy | **0.876** (frozen from epoch 0.2) |
| Eval false_allow | **0.0336** (safety number) |
| Eval false_deny | 0.2948 |
| Eval loss | oscillating 0.26–0.34 (not converging) |

## Reading

- **Left-truncation fix works**: 0.654 (majority) → 0.876 by epoch 0.2. Model sees evidence now.
- **8K saturated at 0.2**: discrete metrics frozen 0.2→1.0. Remaining 12% error is unlearnable at 8K (buried evidence cut even by left-trunc: only last ~32KB chars survive).
- **Mild overfit**: train ↓ 14.9→0.98 while eval loss oscillates. Do not add epochs at 8K.

## Decision

Go 16K (`configs/training/pilot_16k.yaml`, eff 32, 250 steps): buried evidence survives → expect `false_deny` ↓ from 29%. Guard `false_allow` ≤ 3.4%.

## Artifact

`pilot_t3_8192.zip` (user-held, gitignored): `adapter_model.safetensors` 28.8MB verified locally — 226 tensors, exactly 7,198,722 params. Base: `ProCreations/auto-0.4b`.
