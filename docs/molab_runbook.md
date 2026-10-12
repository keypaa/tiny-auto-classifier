# Molab smoke runbook — 200m-2, 50K rows, RTX PRO 6000

*Target: one LoRA smokeutch, ~15–25 min wall clock. Full budget math in
`docs/procreations_training_recipe.md` ("Smoke budget"). Re-upload
`scripts/train_unsloth.py` whenever it changes here — the Molab copy
drifts otherwise.*

## 0. Files needed (2)

- `scripts/train_unsloth.py` — the run itself, self-contained
- Nothing else. Data, tokenizer, and base weights download from HF.

## 1. Environment

```bash
nvidia-smi  # expect RTX PRO 6000, ~96GB; abort otherwise
df -h .     # need ~5GB free (1.1GB data + 0.3GB model + output)
pip install "torch" "transformers==5.16.1" "peft" "datasets" "tokenizers" "numpy" "kernels==0.16.2" "accelerate"
```

Pins are load-bearing: transformers 5.16.1 + kernels 0.16.2 is ProCreations'
exact pair (their `environment.json`). Unpinned `kernels` resolves to 0.17.2
and breaks the hub integration at import — observed, not theorized.

## 2. Dry run (2 min, CPU-only, catches plumbing errors free)

```bash
python3 scripts/train_unsloth.py --base ProCreations/auto-200m-2 --dry-run
```

Expect: `dry-run ok: 200 train rows, 13000 val rows`. Any other output —
stop, paste it back.

## 3. Smoke

```bash
python3 scripts/train_unsloth.py --base ProCreations/auto-200m-2 \
    --max-train 50000 --max-val 1000 \
    --batch 8 --grad-accum 16 --lr 2e-4 --epochs 1 \
    --eval-steps 100 --save-steps 1000 \
    --out runs/m200-smoke
```

Batch 8 is profiled (largest fitting with headroom on 96GB; see recipe
doc). Grad-accum 16 gives 128 rows/update, mirroring their update scale.
Keep the session attended — Molab bans non-interactive jobs.

## 4. Watch for (in order)

1. `attention: flash_attention_2` — if it says `sdpa` or `eager` instead,
   copy the lines above it; the backend chain prints what failed and why.
2. Train loss decreasing across the first 100 steps. Flat or rising: stop,
   do not burn the remaining 300 updates.
3. Eval lines at steps 100/200/300: NLL below base, accuracy sane. NLL
   rising while train loss falls = instant overfit, wrong LR — stop.
4. `saved -> runs/m200-smoke` at the end.

## 5. Resume after a kill

```bash
python3 scripts/train_unsloth.py --base ProCreations/auto-200m-2 \
    --max-train 50000 --max-val 1000 \
    --batch 8 --grad-accum 16 --lr 2e-4 --epochs 1 \
    --eval-steps 100 --save-steps 1000 \
    --out runs/m200-smoke --resume
```

Checkpoints land every 1,000 steps; resume picks up the latest. Verify
with the `resuming from ...` line — `no checkpoint, fresh start` means
the save dir is wrong.

## 6. Bring back

Download the whole `runs/m200-smoke/` directory: `adapter_model.safetensors`,
tokenizer files, `train_config.json`, trainer states. Then here:

```bash
python3 scripts/eval_bench.py --model ProCreations/auto-200m-2 \
    --adapter runs/m200-smoke --n 3000
```

Gates for the 712K run: loss converged, eval NLL at/below base, bench
false-approve did not rise above 4.00%, bench numbers in hand. Any gate
fails → diagnose first; the full run waits.
