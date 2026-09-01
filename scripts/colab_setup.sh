#!/usr/bin/env bash
# Colab T4 12GB — one-shot setup. No bloat.
set -euo pipefail

echo "=== Colab setup T4 12GB ==="
nvidia-smi || echo "no nvidia-smi (CPU fallback)"
python -c "import torch; print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU-only')"

# Lean deps only — transformers already on Colab, but pin to avoid breakage
pip -q install "transformers>=4.40" "peft>=0.11" "accelerate>=0.30" "bitsandbytes>=0.43" "datasets>=2.19" "pyyaml" "psutil" --extra-index-url https://pypi.nvidia.com

# Flash-attn optional for ModernBERT 64K — try but don't fail (heavy)
# pip install flash-attn --no-build-isolation  # uncomment only if needed; 10min build

# Verify policy hash (fail-closed)
python scripts/check_policy.py

# Dry-run each tier at 8K (fits even 15GB RAM) — proves pipeline before real train
for cfg in configs/training/pilot.yaml configs/training/pilot_t1.yaml configs/training/pilot_t4.yaml; do
  echo "--- dry-run $cfg ---"
  python scripts/train.py --config "$cfg" --dry-run
done

echo "=== Ready ==="
echo "Curriculum (manual, lean):"
echo "  for L in 8192 16384 27110; do python scripts/train.py --config configs/training/pilot.yaml --max-length \$L --output-dir models/checkpoints/pilot_t3_\${L}; done"
echo "Then eval on CPU: python scripts/run_baseline_matrix.py (or custom eval)"
