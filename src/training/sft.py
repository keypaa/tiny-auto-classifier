"""SFT — one file, encoder vs decoder, fits T4 12GB via LoRA + checkpointing + fp16."""
from __future__ import annotations

import json
import math
from pathlib import Path

import torch
from transformers import (
    AutoConfig,
    AutoModelForCausalLM,
    AutoModelForSequenceClassification,
    AutoTokenizer,
    Trainer,
    TrainingArguments,
    set_seed,
)

from src.training.config import TrainingConfig
from src.training.dataset import SFTDataset, collate_decoder, collate_encoder


def _auto_target_modules(model_id: str, mode: str) -> list[str]:
    mid = model_id.lower()
    if "modernbert" in mid or "auto-0.4b" in mid:
        # ModernBERT encoder: Wqkv (unified), Wo, Wi (mlp) — verified in HF config
        return ["Wqkv", "Wo", "Wi"]
    if "qwen" in mid:
        return ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj"]
    if "gemma" in mid:
        return ["q_proj", "k_proj", "v_proj", "o_proj"]
    if "lfm" in mid:
        return ["q_proj", "k_proj", "v_proj"]
    return ["q_proj", "v_proj"]


def _pick_precision(cfg: TrainingConfig) -> str:
    # T4 (Turing) has fp16, not bf16. Try bf16 only if A100/H100.
    if cfg.precision == "bf16" and not torch.cuda.is_available():
        return "fp32"
    if cfg.precision == "bf16":
        try:
            if not torch.cuda.is_bf16_supported():
                print("bf16 not supported on this GPU (T4) — falling back to fp16")
                return "fp16"
        except Exception:
            return "fp16"
    return cfg.precision


def build_trainer(cfg: TrainingConfig) -> Trainer:
    cfg.validate()
    set_seed(cfg.seed)

    # tokenizer — use slow path for 65K? fast is fine
    tok = AutoTokenizer.from_pretrained(cfg.model_id, trust_remote_code=True)
    # decoder needs pad_token
    if cfg.mode == "decoder" and tok.pad_token is None:
        tok.pad_token = tok.eos_token
        print(f"Set pad_token to eos_token: {tok.pad_token}")

    # datasets — hash-checked at load
    train_ds = SFTDataset(cfg.train_file, tok, cfg.policy_path, cfg.mode, cfg.max_length, verify_policy=True)
    eval_ds = None
    if cfg.val_file and Path(cfg.val_file).exists():
        eval_ds = SFTDataset(cfg.val_file, tok, cfg.policy_path, cfg.mode, cfg.max_length, verify_policy=False)

    # guard: ModernBERT at 27K without flash_attn OOMs (dense mask)
    if cfg.mode == "encoder" and "modernbert" in cfg.model_id.lower() and cfg.max_length > 8192:
        try:
            import flash_attn  # noqa: F401

            print("flash_attn available — 27K+ will use sparse path")
        except ImportError:
            print("WARNING: flash_attn not installed — ModernBERT >8K will materialize dense mask and OOM on T4. Install flash_attn or stay at 8K.")

    # precision for model dtype — must match TrainingArguments scaler (T4 fp16, not bf16)
    _prec = _pick_precision(cfg)
    _dtype = torch.bfloat16 if _prec == "bf16" else torch.float16 if _prec == "fp16" else torch.float32
    # model
    if cfg.mode == "encoder":
        config = AutoConfig.from_pretrained(cfg.model_id, trust_remote_code=True)
        # ensure 2 labels (approve/deny) — don't trust remote id2label blindly
        config.num_labels = 2
        config.id2label = {0: "approve", 1: "deny"}
        config.label2id = {"approve": 0, "deny": 1}
        model = AutoModelForSequenceClassification.from_pretrained(
            cfg.model_id, config=config, trust_remote_code=True, dtype=_dtype
        )
    else:
        model = AutoModelForCausalLM.from_pretrained(
            cfg.model_id, trust_remote_code=True, dtype=_dtype
        )
        # resize if needed (Gemma large vocab)
        if len(tok) != model.get_input_embeddings().weight.shape[0]:
            model.resize_token_embeddings(len(tok))

    # gradient checkpointing — must be before LoRA
    if cfg.grad_checkpointing:
        try:
            model.gradient_checkpointing_enable()
            # ModernBERT needs this flag
            if hasattr(model.config, "deterministic_flash_attn"):
                pass
        except Exception as e:
            print(f"gradient_checkpointing_enable failed: {e}")

    # LoRA
    if cfg.lora.enabled:
        try:
            from peft import LoraConfig, get_peft_model, TaskType

            target = cfg.lora.target_modules or _auto_target_modules(cfg.model_id, cfg.mode)
            task = TaskType.SEQ_CLS if cfg.mode == "encoder" else TaskType.CAUSAL_LM
            peft_cfg = LoraConfig(
                r=cfg.lora.r,
                lora_alpha=cfg.lora.alpha,
                lora_dropout=cfg.lora.dropout,
                target_modules=target,
                task_type=task,
                bias="none",
            )
            model = get_peft_model(model, peft_cfg)
            model.print_trainable_parameters()
        except ImportError as e:
            print(f"peft import failed ({e}) — training full model (will OOM at 27K on T4)")
            import traceback

            traceback.print_exc()
        except Exception as e:
            print(f"LoRA setup failed ({target}): {e} — falling back to full")
            import traceback

            traceback.print_exc()

    # collator
    if cfg.mode == "encoder":
        pad_id = tok.pad_token_id if tok.pad_token_id is not None else 50283
        collator = lambda batch: collate_encoder(batch, pad_token_id=pad_id)
    else:
        pad_id = tok.pad_token_id
        collator = lambda batch: collate_decoder(batch, pad_token_id=pad_id)

    # precision + optimizer
    prec = _pick_precision(cfg)
    use_bf16 = prec == "bf16"
    use_fp16 = prec == "fp16"
    # 8bit optimizer only if bitsandbytes available and not conflicting with fp16 scaler
    # T4 fp16 + adamw_8bit + GradScaler + clip_grad_norm triggers
    #   ValueError: Attempting to unscale FP16 gradients / BFloat16 not implemented
    optim = "adamw_8bit" if cfg.optimizer == "adamw_8bit" else "adamw_torch"
    if (use_fp16 or use_bf16) and optim == "adamw_8bit":
        print(f"{prec} + adamw_8bit is broken on this torch/accelerate (scaler) — falling back to adamw_torch")
        optim = "adamw_torch"
    try:
        import bitsandbytes  # noqa: F401
    except ImportError:
        if optim == "adamw_8bit":
            print("bitsandbytes not installed — falling back to adamw_torch")
            optim = "adamw_torch"

    # TrainingArguments — minimal, curriculum-aware (compat for transformers 4.x and 5.x)
    # torch 2.8 fp16 GradScaler + clip_grad_norm is broken on T4 (unscale FP16) → disable clip for fp16
    _max_grad_norm = 1.0
    if use_fp16:
        print("fp16 on T4 with torch 2.8 scaler broken for clip — disabling max_grad_norm (no clip) to keep fp16 speed")
        _max_grad_norm = None  # Trainer will pass None → accelerate skips unscale+clip
    # warmup_ratio only in 4.44+ / 5.x, eval_strategy renamed in 5.x
    import inspect as _inspect

    _ta_params = set(_inspect.signature(TrainingArguments.__init__).parameters.keys())
    _ta_kwargs: dict = dict(
        output_dir=cfg.output_dir,
        per_device_train_batch_size=cfg.per_device_batch,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=cfg.grad_accum,
        num_train_epochs=cfg.epochs,
        learning_rate=cfg.lr,
        weight_decay=cfg.weight_decay,
        logging_steps=cfg.logging_steps,
        save_steps=cfg.save_steps,
        save_total_limit=2,
        bf16=use_bf16,
        fp16=use_fp16,
        optim=optim,
        gradient_checkpointing=cfg.grad_checkpointing,
        dataloader_num_workers=cfg.num_workers,
        dataloader_pin_memory=False,  # T4 15GB RAM — pin wastes
        report_to="none",
        seed=cfg.seed,
        ddp_find_unused_parameters=False,
        remove_unused_columns=False,
    )
    if _max_grad_norm is not None:
        _ta_kwargs["max_grad_norm"] = _max_grad_norm
    # warmup
    if "warmup_ratio" in _ta_params:
        _ta_kwargs["warmup_ratio"] = cfg.warmup_ratio
    else:
        # fallback: warmup_steps = ratio * steps per epoch (approx)
        _ta_kwargs["warmup_steps"] = max(1, int(len(train_ds) // cfg.effective_batch * cfg.epochs * cfg.warmup_ratio))
    # eval strategy name compat
    _eval_key = "eval_strategy" if "eval_strategy" in _ta_params else "evaluation_strategy"
    _ta_kwargs[_eval_key] = "steps" if eval_ds else "no"
    if eval_ds:
        _ta_kwargs["eval_steps"] = cfg.save_steps
    args = TrainingArguments(**_ta_kwargs)

    def compute_metrics(eval_pred):
        # encoder: logits vs 0/1
        if cfg.mode == "encoder":
            logits, labels = eval_pred
            preds = logits.argmax(-1)
            acc = (preds == labels).mean()
            # false allow = predicted ALLOW (0) but true BLOCK (1)
            fa = ((preds == 0) & (labels == 1)).sum() / max(1, (labels == 1).sum())
            fd = ((preds == 1) & (labels == 0)).sum() / max(1, (labels == 0).sum())
            return {"accuracy": float(acc), "false_allow": float(fa), "false_deny": float(fd)}
        else:
            # decoder: we only scored full labels earlier; simplest: exact match on target decoding
            # eval_pred is (logits, labels) with -100 masked — Trainer doesn't decode, so approximate
            return {}

    # Trainer compat: 4.x uses tokenizer=, 5.x uses processing_class=
    import inspect as _inspect2

    _trainer_params = set(_inspect2.signature(Trainer.__init__).parameters.keys())
    _trainer_kwargs = dict(
        model=model,
        args=args,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        data_collator=collator,
        compute_metrics=compute_metrics if eval_ds and cfg.mode == "encoder" else None,
    )
    if "processing_class" in _trainer_params:
        _trainer_kwargs["processing_class"] = tok
    else:
        _trainer_kwargs["tokenizer"] = tok
    trainer = Trainer(**_trainer_kwargs)
    return trainer


def train(cfg_path: str | Path) -> str:
    cfg = TrainingConfig.from_yaml(cfg_path)
    print(f"Loaded {cfg_path}: model={cfg.model_id} mode={cfg.mode} max_length={cfg.max_length} batch={cfg.effective_batch}")
    trainer = build_trainer(cfg)
    # sanity: log one batch shape
    batch = next(iter(trainer.get_train_dataloader()))
    print(f"Sanity batch: input_ids {batch['input_ids'].shape} labels {batch['labels'].shape} dtype {batch['input_ids'].dtype}")
    result = trainer.train()
    # save
    trainer.save_model(cfg.output_dir)
    # also save tokenizer + config snapshot + metrics
    trainer.tokenizer.save_pretrained(cfg.output_dir)
    Path(cfg.output_dir, "trainer_state.json").write_text(json.dumps({"best": str(result)}, indent=2))
    print(f"Done → {cfg.output_dir}")
    return cfg.output_dir
