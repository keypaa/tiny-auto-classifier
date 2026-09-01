#!/usr/bin/env python3
"""CLI — run on Colab T4 or local. Minimal, no overengineering.

Usage:
  python scripts/train.py --config configs/training/pilot.yaml
  python scripts/train.py --config configs/training/pilot_t3.yaml --max-length 27110

Curriculum example (manual, keep it simple):
  for L in 8192 16384 27110; do python scripts/train.py --config configs/training/pilot.yaml --max-length $L --output-dir models/checkpoints/pilot_$L; done
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# allow `python scripts/train.py` from any cwd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.training.config import TrainingConfig
from src.training.sft import train


def main():
    ap = argparse.ArgumentParser(description="Tiny Auto Classifier SFT")
    ap.add_argument("--config", required=True, help="YAML config path")
    ap.add_argument("--max-length", type=int, default=None, help="Override max_length (curriculum step)")
    ap.add_argument("--output-dir", type=str, default=None, help="Override output_dir")
    ap.add_argument("--dry-run", action="store_true", help="Validate config + one batch, no training")
    args = ap.parse_args()

    cfg = TrainingConfig.from_yaml(args.config)
    if args.max_length:
        cfg.max_length = args.max_length
    if args.output_dir:
        cfg.output_dir = args.output_dir
    cfg.validate()
    print(f"Config: {cfg.model_id} mode={cfg.mode} max_length={cfg.max_length} batch={cfg.effective_batch} lora={cfg.lora.enabled} prec={cfg.precision}")

    if args.dry_run:
        from src.training.sft import build_trainer

        trainer = build_trainer(cfg)
        batch = next(iter(trainer.get_train_dataloader()))
        print(f"Dry-run batch OK: input_ids {tuple(batch['input_ids'].shape)} labels {tuple(batch['labels'].shape)}")
        # also check VRAM estimate
        try:
            import torch

            if torch.cuda.is_available():
                print(f"CUDA: {torch.cuda.get_device_name(0)} free {torch.cuda.mem_get_info()[0]/1e9:.1f}GB / {torch.cuda.mem_get_info()[1]/1e9:.1f}GB")
        except Exception:
            pass
        print("Dry-run passed — ready for real train (remove --dry-run)")
        return

    out = train(args.config if not (args.max_length or args.output_dir) else _write_tmp_cfg(cfg, args.config))
    print(f"Training complete → {out}")


def _write_tmp_cfg(cfg: TrainingConfig, base: str) -> str:
    import tempfile, yaml, dataclasses

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    # dataclasses.asdict handles nested
    import dataclasses

    d = dataclasses.asdict(cfg)
    yaml.safe_dump(d, tmp)
    tmp.close()
    return tmp.name


if __name__ == "__main__":
    main()
