#!/usr/bin/env python3
"""
Stage 0 diagnostic D — real ONNX INT8 multiplier on THIS CPU.

Replaces projected "INT8 is 2-4x" claims with a measured number on the actual
deployment target. Times a single 8K forward under:
  torch fp32 eager (our current 102s baseline)
  onnxruntime fp32
  onnxruntime INT8 (dynamic quant)
and reports decision agreement so we know whether quantization is even safe.

Usage:
  python scripts/diag_onnx.py --adapter models/checkpoints/pilot_t3_8192 --iters 3
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import torch
from transformers import AutoConfig, AutoModelForSequenceClassification, AutoTokenizer

from src.prompt.builder import PromptBuilder


def timeit(fn, iters: int, warmup: int = 1):
    for _ in range(warmup):
        fn()
    ts = []
    for _ in range(iters):
        t0 = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t0)
    return float(np.median(ts)), float(np.percentile(ts, 95))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default="models/checkpoints/pilot_t3_8192")
    ap.add_argument("--max-length", type=int, default=8192)
    ap.add_argument("--iters", type=int, default=3)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--skip-fp32-torch", action="store_true", help="skip the 100s-per-forward baseline")
    ap.add_argument("--out", default="reports/stage0_onnx.json")
    args = ap.parse_args()

    torch.set_num_threads(args.threads)
    base = json.loads(Path(args.adapter, "adapter_config.json").read_text())["base_model_name_or_path"]

    tok = AutoTokenizer.from_pretrained(base, trust_remote_code=True)
    tok.truncation_side = "left"
    pb = PromptBuilder("prompts/original/policy.txt")
    prompt, _ = pb.build(transcript="user: delete /tmp/foo using rm -rf /tmp/foo",
                         latest_action="rm -rf /tmp/foo", verify=False)
    enc = tok(prompt, truncation=True, max_length=args.max_length, return_attention_mask=True)
    ids = np.array([enc["input_ids"]], dtype=np.int64)
    msk = np.array([enc["attention_mask"]], dtype=np.int64)
    print(f"input: {ids.shape[1]} tokens, threads={args.threads}")

    res: dict = {"tokens": int(ids.shape[1]), "threads": args.threads}

    # ---- torch fp32 (our current baseline) ----
    if not args.skip_fp32_torch:
        print("exporting merged model for torch reference...")
        cfg = AutoConfig.from_pretrained(base, trust_remote_code=True)
        cfg.num_labels = 2
        m = AutoModelForSequenceClassification.from_pretrained(base, config=cfg,
                                                              trust_remote_code=True, dtype=torch.float32)
        from peft import PeftModel
        m = PeftModel.from_pretrained(m, args.adapter).merge_and_unload().eval()
        t = torch.tensor(ids); tmask = torch.tensor(msk)
        with torch.no_grad():
            med, p95 = timeit(lambda: m(input_ids=t, attention_mask=tmask), args.iters, warmup=1)
        res["torch_fp32_merged"] = {"p50_s": round(med, 2), "p95_s": round(p95, 2),
                                    "logits": m(input_ids=t, attention_mask=tmask).logits[0].tolist()}
        print(f"  torch fp32: p50 {med:.2f}s p95 {p95:.2f}s")
        del m

    # ---- ONNX ----
    try:
        import onnxruntime as ort
    except ImportError:
        print("onnxruntime not installed — pip install onnxruntime")
        return
    print(f"onnxruntime {ort.__version__}, providers: {ort.get_available_providers()}")

    onnx_dir = Path("models/exports/onnx_fp32")
    if not (onnx_dir / "model.onnx").exists():
        onnx_dir.mkdir(parents=True, exist_ok=True)
        print("exporting ONNX fp32 (this takes a minute)...")
        cfg = AutoConfig.from_pretrained(base, trust_remote_code=True)
        cfg.num_labels = 2
        m = AutoModelForSequenceClassification.from_pretrained(base, config=cfg,
                                                              trust_remote_code=True, dtype=torch.float32)
        from peft import PeftModel
        m = PeftModel.from_pretrained(m, args.adapter).merge_and_unload().eval()
        m.save_pretrained(onnx_dir, safe_serialization=True)
        tok.save_pretrained(onnx_dir)

    so = ort.SessionOptions()
    so.intra_op_num_threads = args.threads
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    s32 = ort.InferenceSession(str(onnx_dir / "model.onnx"), so, providers=["CPUExecutionProvider"])
    out32 = s32.run(None, {"input_ids": ids, "attention_mask": msk})[0][0]
    med32, p95_32 = timeit(lambda: s32.run(None, {"input_ids": ids, "attention_mask": msk}), args.iters)
    res["onnx_fp32"] = {"p50_s": round(med32, 3), "p95_s": round(p95_32, 3), "logits": out32.tolist()}
    print(f"  onnx fp32: p50 {med32*1000:.0f}ms p95 {p95_32*1000:.0f}ms")

    # ---- INT8 dynamic ----
    from onnxruntime.quantization import QuantType, quantize_dynamic
    qdir = Path("models/exports/onnx_int8")
    if not (qdir / "model.onnx").exists():
        qdir.mkdir(parents=True, exist_ok=True)
        print("quantizing to INT8 dynamic...")
        quantize_dynamic(str(onnx_dir / "model.onnx"), str(qdir / "model.onnx"),
                         weight_type=QuantType.QInt8)
        for f in onnx_dir.iterdir():
            if f.name != "model.onnx" and f.suffix in (".json", ".txt"):
                (qdir / f.name).write_bytes(f.read_bytes())
    s8 = ort.InferenceSession(str(qdir / "model.onnx"), so, providers=["CPUExecutionProvider"])
    out8 = s8.run(None, {"input_ids": ids, "attention_mask": msk})[0][0]
    med8, p95_8 = timeit(lambda: s8.run(None, {"input_ids": ids, "attention_mask": msk}), args.iters)
    res["onnx_int8_dynamic"] = {"p50_s": round(med8, 3), "p95_s": round(p95_8, 3), "logits": out8.tolist()}
    print(f"  onnx int8: p50 {med8*1000:.0f}ms p95 {p95_8*1000:.0f}ms")

    d32 = np.array(res["onnx_fp32"]["logits"]); d8 = np.array(res["onnx_int8_dynamic"]["logits"])
    res["agreement_int8_vs_fp32"] = {
        "argmax_same": bool(d32.argmax() == d8.argmax()),
        "max_abs_logit_delta": round(float(np.abs(d32 - d8).max()), 5),
        "p_block_fp32": round(float(1 / (1 + np.exp(-d32[1] + d32[0]))), 4),
        "p_block_int8": round(float(1 / (1 + np.exp(-(d8[1] - d8[0])))), 4),
    }
    res["measured_speedup_onnx_int8_vs_onnx_fp32"] = round(med32 / med8, 2)
    if "torch_fp32_merged" in res:
        res["measured_speedup_onnx_int8_vs_torch_fp32"] = round(res["torch_fp32_merged"]["p50_s"] / med8, 1)
        res["extrapolated_encoder_27k_s_int8"] = round(med8 * 2.9, 1)  # FLOP ratio 27K/8K for this arch

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(res, indent=2))
    print("\n" + json.dumps(res, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
