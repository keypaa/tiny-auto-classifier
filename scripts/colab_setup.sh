#!/usr/bin/env bash
# Colab T4 12GB — one-shot setup. No bloat.
set -euo pipefail

echo "=== Colab setup T4 12GB ==="
nvidia-smi || echo "no nvidia-smi (CPU fallback)"
python -c "import torch; print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU-only')"

# Keep Colab torch 2.11 (torchvision 0.26 matches), pin accelerate 0.34 to fix fp16 scaler (1.10 breaks unscale)
pip -q install "accelerate==0.34.0" 2>&1 | tail -n 3
# Lean deps only — transformers already on Colab, but pin to avoid breakage (keep accelerate 0.34)
pip -q install "transformers>=4.40" "peft>=0.11" "bitsandbytes>=0.43" "datasets>=2.19" "pyyaml" "psutil" --extra-index-url https://pypi.nvidia.com
# ensure peft is actually importable (Colab sometimes has stale transformers)
python -c "import peft; print(f'peft {peft.__version__} ok')" || pip -q install --no-deps peft
python -c "import torch; print(f'torch {torch.__version__} cuda {torch.version.cuda}')" 

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
