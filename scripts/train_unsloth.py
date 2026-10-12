#!/usr/bin/env python3
"""Fine-tune a ProCreations encoder (200m-2 / 0.4b-2) for approve/deny.

Thin wrapper around proven pieces only — no custom modeling code:
  - Unsloth FastModel (bert_classification recipe: AutoModelForSequence-
    Classification + LoRA r16 SEQ_CLS) when importable,
  - otherwise plain transformers + peft with identical hyperparameters,
    so results compare across platforms.

Data: ProCreations/auto-1b-data {text, label} as-is (their format, no
policy). Labels approve->0 deny->1 match their id2label. Metrics follow
their leaderboard protocol: threshold 0.5, deny is positive, and we
report false-approve/false-deny alongside accuracy.

Truncation note: default max_len 8192 with RIGHT truncation (their
recipe). Long-context needles past the cutoff are lost either way; the
base weights already carry 64K RoPE, fine-tuning at 8K approximately
preserves it. If you can afford 64K training, raise --max-len.

Runs on: Colab T4 (free), Modal GPU, or any local CUDA box. No GPU here,
so this script is prepare-only on CPU (use --dry-run to validate plumbing).

Molab (RTX PRO 6000 96GB) — current reference platform:
    !pip install "torch" "transformers==5.16.1" "peft" "datasets" "tokenizers" "numpy" "kernels==0.16.2"
    !python train_unsloth.py --base ProCreations/auto-200m-2 \\
        --max-train 50000 --max-val 1000 \\
        --batch 8 --grad-accum 16 --lr 2e-4 --epochs 1 \\
        --eval-steps 100 --save-steps 1000 \\
        --out runs/m200-smoke
Full procedure (preflight, watch-items, resume, eval gates):
docs/molab_runbook.md.
Notes: pinned versions mirror ProCreations' environment.json; `kernels`
enables FA2 (falls back to SDPA with a log line without it). `unsloth`
package optional — used when importable, otherwise the
identical-hyperparameter plain path. Batch 8 profiled on 96GB;
grad-accum 16 gives their 128-row update scale.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

LABEL2ID = {"approve": 0, "deny": 1}
ID2LABEL = {0: "approve", 1: "deny"}


def load_data(train_src: str, val_src: str, max_train: int, tokenizer,
              max_len: int, args_max_val: int = 0):
    from datasets import load_dataset

    def prep(split_src: str, split: str | None):
        if split_src == "hf":
            ds = load_dataset("ProCreations/auto-1b-data", split=split)
        else:
            ds = load_dataset("json", data_files=split_src, split="train")
        rows = ds.to_list()
        return rows

    train_rows = prep(train_src, "train[:712000]" if train_src == "hf" else None)
    if max_train > 0:
        import random

        train_rows = random.Random(7).sample(train_rows, min(max_train, len(train_rows)))
    val_rows = prep(val_src, "validation") if val_src else []
    if val_src == "hf-skip":
        val_rows = []
    if args_max_val > 0:
        import random as _rng

        val_rows = _rng.Random(11).sample(val_rows, min(args_max_val, len(val_rows)))

    def tok(rows):
        texts = [r["text"] for r in rows]
        enc = tokenizer(texts, truncation=True, max_length=max_len)
        return [{"input_ids": ids, "attention_mask": m,
                 "labels": LABEL2ID[r["label"]]}
                for ids, m, r in zip(enc["input_ids"], enc["attention_mask"], rows)]

    return tok(train_rows), tok(val_rows)


def compute_metrics_fn(eval_pred):
    import numpy as np

    logits, labels = eval_pred
    preds = np.asarray(logits).argmax(-1)
    labels = np.asarray(labels)
    tp = int(((labels == 1) & (preds == 1)).sum())
    tn = int(((labels == 0) & (preds == 0)).sum())
    fp = int(((labels == 1) & (preds == 0)).sum())
    fn = int(((labels == 0) & (preds == 1)).sum())
    out = {"accuracy": (tp + tn) / max(1, len(labels))}
    if tp + fp:
        out["false_approve"] = fp / (tp + fp)
    if tn + fn:
        out["false_deny"] = fn / (tn + fn)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="ProCreations/auto-200m-2")
    ap.add_argument("--train-src", default="hf")
    ap.add_argument("--val-src", default="hf")
    ap.add_argument("--max-train", type=int, default=0,
                    help="0 = all 712K; use 50000 for smoke runs")
    ap.add_argument("--max-val", type=int, default=0,
                    help="0 = full 13K val; use 1000 for smoke runs")
    ap.add_argument("--max-len", type=int, default=8192)
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--grad-accum", type=int, default=1)
    ap.add_argument("--eval-steps", type=int, default=500)
    ap.add_argument("--save-steps", type=int, default=2000)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--out", default="runs/m200-a1b")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true",
                    help="validate data plumbing on CPU, no training")
    ap.add_argument("--resume", action="store_true",
                    help="resume from latest checkpoint in --out")
    args = ap.parse_args()

    try:
        from unsloth import FastModel

        have_unsloth = True
    except ImportError:
        have_unsloth = False
        print("unsloth unavailable -> plain transformers+peft path")

    if args.dry_run:
        from transformers import AutoTokenizer

        tok = AutoTokenizer.from_pretrained(args.base, trust_remote_code=True)
        tr, va = load_data(args.train_src, args.val_src, 200, tok, args.max_len)
        print(f"dry-run ok: {len(tr)} train rows, {len(va)} val rows, "
              f"max tokens {max(len(r['input_ids']) for r in tr)}")
        return 0

    import torch
    from transformers import (AutoModelForSequenceClassification,
                              AutoTokenizer, Trainer, TrainingArguments)

    tok = AutoTokenizer.from_pretrained(args.base, trust_remote_code=True)
    train_ds, val_ds = load_data(args.train_src, args.val_src,
                                 args.max_train, tok, args.max_len,
                                 args.max_val)
    print(f"train={len(train_ds)} val={len(val_ds)} max_len={args.max_len}")

    if have_unsloth:
        from transformers import AutoModelForSequenceClassification as AMS

        model, _tok = FastModel.from_pretrained(
            model_name=args.base, auto_model=AMS, max_seq_length=args.max_len,
            dtype=None, num_labels=2, full_finetuning=False,
            id2label=ID2LABEL, label2id=LABEL2ID, load_in_4bit=False)
        from unsloth import FastModel as FM

        model = FM.get_peft_model(
            model, r=args.lora_r, lora_alpha=args.lora_r, lora_dropout=0,
            bias="none", use_gradient_checkpointing="unsloth",
            random_state=args.seed, use_rslora=False, task_type="SEQ_CLS")
    else:
        from peft import LoraConfig, get_peft_model

        # Native BF16 weights (the checkpoints ship BF16; loading fp32
        # doubles weight memory for no reason). Attention: fa2 -> sdpa ->
        # eager. FA2 needs sm_80+ (L4 yes, T4 no) and the flash-attn package;
        # each fallback prints so the log shows what actually ran.
        def _load(attn):
            return AutoModelForSequenceClassification.from_pretrained(
                args.base, trust_remote_code=True, num_labels=2,
                id2label=ID2LABEL, label2id=LABEL2ID,
                dtype=torch.bfloat16,
                **({"attn_implementation": attn} if attn else {}))
        model = None
        for attn in ("flash_attention_2", "sdpa", None):
            try:
                model = _load(attn)
                print(f"attention: {attn or 'eager'}, weights: bf16")
                break
            except Exception as exc:
                print(f"attn {attn or 'eager'} failed:\n{exc}")
        if model is None:
            raise SystemExit("no attention backend worked")
        # ModernBERT linear layers (their names, matching our pilot adapter)
        model = get_peft_model(model, LoraConfig(
            r=args.lora_r, lora_alpha=args.lora_r, lora_dropout=0.0,
            bias="none", task_type="SEQ_CLS",
            target_modules=["Wqkv", "Wi", "Wo"]))
        # Without this, 8K activations alone OOM a 22GB card (observed).
        model.gradient_checkpointing_enable()
    model.print_trainable_parameters()

    # Optimizer mirrors ProCreations' published recipe (training/train.py in
    # ProCreations/auto-200m-2): fused AdamW, betas (0.9, 0.95), wd 0.01,
    # grad clip 1.0, 3% warmup, cosine to 10% of peak. Their master weights
    # are fp32; ours load bf16 (22GB vs their 96GB) with bf16 autocast, same
    # as their compute precision. No bitsandbytes: LoRA states are ~36MB,
    # 8bit saves nothing measurable and deviates from evidence.
    optim = "adamw_torch_fused"
    targs = TrainingArguments(
        output_dir=args.out, per_device_train_batch_size=args.batch,
        gradient_accumulation_steps=args.grad_accum,
        per_device_eval_batch_size=args.batch * 2,
        num_train_epochs=args.epochs, learning_rate=args.lr,
        warmup_ratio=0.03, lr_scheduler_type="cosine",
        max_grad_norm=1.0,
        fp16=not torch.cuda.is_bf16_supported() if torch.cuda.is_available() else False,
        bf16=torch.cuda.is_bf16_supported() if torch.cuda.is_available() else False,
        logging_steps=25, eval_strategy="steps", eval_steps=args.eval_steps,
        save_strategy="steps", save_steps=args.save_steps, save_total_limit=2,
        seed=args.seed, report_to="none",
        optim=optim,
    )
    trainer = Trainer(model=model, args=targs,
                      train_dataset=train_ds, eval_dataset=val_ds or None,
                      processing_class=tok, compute_metrics=compute_metrics_fn)
    ckpt = None
    if args.resume:
        ckpts = sorted(Path(args.out).glob("checkpoint-*"),
                       key=lambda p: int(p.name.split("-")[-1]))
        ckpt = str(ckpts[-1]) if ckpts else None
        print(f"resuming from {ckpt}" if ckpt else "no checkpoint, fresh start")
    trainer.train(resume_from_checkpoint=ckpt)
    trainer.save_model(args.out)
    tok.save_pretrained(args.out)
    Path(args.out, "train_config.json").write_text(json.dumps(vars(args),
                                                              indent=2, default=str))
    print(f"saved -> {args.out}; eval with:")
    print(f"  python3 scripts/eval_bench.py --model {args.base} "
          f"--adapter {args.out} --n 3000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())