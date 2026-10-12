"""Full 3,000-row approve-or-deny eval on a Modal T4.

Our adapter (LoRA on ProCreations/auto-0.4b), batched fp16. Base model and
bench download from HF inside the container; only the 122MB adapter dir
uploads from local. Same metric protocol as scripts/eval_bench.py:
threshold 0.5, deny is the positive class.

Usage:
    modal run modal_eval.py
"""

import modal

IMAGE = (
    modal.Image.debian_slim(python_version="3.11")
    # Pinned to the exact set proven working locally (transformers 5.19 +
    # torch 2.5 crashes in masking_utils on import). PyPI torch wheels
    # bundle CUDA; T4 sm_75 is supported.
    .pip_install("torch", "transformers==5.12.1", "peft", "datasets",
                 "tokenizers>=0.19")
    .add_local_dir(
        "/home/keypaa/Projects/tiny-auto-classifier/models/checkpoints/pilot_t3_8192",
        remote_path="/adapter",
    )
)
APP = modal.App("tiny-auto-classifier-bench")

BASE_MODEL = "ProCreations/auto-0.4b"
MAX_LEN = 8192
# Batch by token budget, not row count: rows range from ~100 to 8192
# tokens. 32K-token batches fit comfortably; L4 headroom allows more.
TOKEN_BUDGET = 65536


# L4 over T4: ~2x throughput for ~1.3x $/hr, and 22GB lets the token
# budget rise. T4 proved too slow at 0.7 rows/s on 8K batches.
@APP.function(image=IMAGE, gpu="L4", timeout=1800)
def run_full_bench(start: int = 0, end: int | None = None,
                   model_id: str = BASE_MODEL,
                   use_adapter: bool = True) -> dict:
    import time

    import torch
    from datasets import load_dataset
    from peft import PeftModel
    from transformers import (AutoModelForSequenceClassification,
                              AutoTokenizer)

    assert torch.cuda.is_available(), "no CUDA on worker"
    print(f"gpu: {torch.cuda.get_device_name(0)}", flush=True)

    ds = load_dataset("ProCreations/approve-or-deny", split="test")
    if end is None:
        end = len(ds)
    print(f"bench rows {start}..{end} of {len(ds)}", flush=True)

    tok = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    try:
        base = AutoModelForSequenceClassification.from_pretrained(
            model_id, trust_remote_code=True, torch_dtype=torch.float16,
            attn_implementation="sdpa",
        ).cuda()
        print("attention: sdpa", flush=True)
    except Exception as exc:
        print(f"sdpa unsupported ({exc}); falling back to eager", flush=True)
        base = AutoModelForSequenceClassification.from_pretrained(
            model_id, trust_remote_code=True, torch_dtype=torch.float16,
        ).cuda()
    tag = model_id
    if use_adapter:
        model = PeftModel.from_pretrained(base, "/adapter").eval()
        tag += "+pilot_t3_8192"
    else:
        model = base.eval()
    id2label = {int(k): v for k, v in model.config.id2label.items()}
    if not id2label and hasattr(model, "base_model"):
        id2label = {int(k): v for k, v in model.base_model.config.id2label.items()}

    tp = tn = fp = fn = 0
    lat: list[float] = []
    t_all = time.monotonic()
    scope = list(range(start, end))
    print(f"starting {len(scope)} rows, token_budget={TOKEN_BUDGET}, "
          f"max_len={MAX_LEN}", flush=True)
    t0 = time.monotonic()
    all_ids = tok([ds[int(i)]["text"] for i in scope],
                  truncation=True, max_length=MAX_LEN)["input_ids"]
    lens = [len(x) for x in all_ids]
    print(f"  pre-tokenized {len(lens)} rows in {time.monotonic()-t0:.0f}s; "
          f"max len {max(lens)}", flush=True)
    order = sorted(range(len(scope)), key=lambda i: -lens[i])
    batches: list[list[int]] = []
    cur: list[int] = []
    curtok = 0
    for i in order:
        if cur and curtok + lens[i] > TOKEN_BUDGET:
            batches.append(cur)
            cur, curtok = [], 0
        cur.append(i)
        curtok += lens[i]
    if cur:
        batches.append(cur)
    print(f"  {len(batches)} batches", flush=True)
    for b, chunk in enumerate(batches):
        texts = [ds[int(scope[i])]["text"] for i in chunk]
        enc = tok(texts, return_tensors="pt", truncation=True,
                  max_length=MAX_LEN, padding=True).to("cuda")
        seqlen = int(enc["input_ids"].shape[1])
        t0 = time.monotonic()
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
            logits = model(**enc).logits
        dt = time.monotonic() - t0
        lat.append(dt / len(chunk))
        # allocated = live tensors; reserved = what nvidia-smi sees
        # (context + allocator cache). Report both, they disagree by GBs.
        mem = (f"{torch.cuda.memory_allocated()/1e9:.1f}/"
               f"{torch.cuda.memory_reserved()/1e9:.1f}GB")
        for i, p in zip(chunk, logits.argmax(-1).tolist()):
            truth = ds[int(scope[i])]["label"]
            pred = id2label[int(p)]
            if truth == "deny" and pred == "deny":
                tp += 1
            elif truth == "approve" and pred == "approve":
                tn += 1
            elif truth == "deny":
                fp += 1
            else:
                fn += 1
        done = sum(len(c) for c in batches[:b + 1])
        if done % 200 == 0 or done == len(scope):
            el = time.monotonic() - t_all
            rate = done / el
            eta = (len(scope) - done) / rate if rate > 0 else -1
            print(f"  {done}/{len(scope)}  {rate:.1f} rows/s  "
                  f"eta {eta:.0f}s  batch {dt:.2f}s/{seqlen}tok  "
                  f"gpu {mem} alloc/res", flush=True)

    n = tp + tn + fp + fn
    return {
        "model": tag,
        "n": n,
        "accuracy": round((tp + tn) / n, 4),
        "balanced_acc": round(((tp / (tp + fn)) + (tn / (tn + fp))) / 2, 4),
        "false_approve": round(fp / (tp + fp), 4),
        "false_deny": round(fn / (tn + fn), 4),
        "latency_s": round(sum(lat) / len(lat), 4),
        "confusion": {"tp_deny": tp, "tn_approve": tn,
                      "fp_waved": fp, "fn_blocked": fn},
    }


@APP.function(image=IMAGE, gpu="T4", timeout=600)
def smoke_batch() -> dict:
    """One batch, eight rows, with stage-by-stage timing. Fast fail signal."""
    import time

    import torch
    from datasets import load_dataset
    from peft import PeftModel
    from transformers import (AutoModelForSequenceClassification,
                              AutoTokenizer)

    assert torch.cuda.is_available()
    ds = load_dataset("ProCreations/approve-or-deny", split="test")
    tok = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    base = AutoModelForSequenceClassification.from_pretrained(
        BASE_MODEL, trust_remote_code=True, torch_dtype=torch.float16,
    ).cuda()
    model = PeftModel.from_pretrained(base, "/adapter").eval()

    out: dict = {}
    t0 = time.monotonic()
    texts = [ds[int(i)]["text"] for i in range(8)]
    out["fetch_s"] = round(time.monotonic() - t0, 2)
    t0 = time.monotonic()
    enc = tok(texts, return_tensors="pt", truncation=True,
              max_length=MAX_LEN, padding=True).to("cuda")
    out["tokenize_s"] = round(time.monotonic() - t0, 2)
    out["seqlen"] = int(enc["input_ids"].shape[1])
    t0 = time.monotonic()
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
        logits = model(**enc).logits
    out["forward_s"] = round(time.monotonic() - t0, 2)
    out["mem_gb"] = round(torch.cuda.memory_allocated() / 1e9, 2)
    out["logits0"] = [round(float(v), 3) for v in logits[0].tolist()]
    return out


@APP.local_entrypoint()
def main(smoke: bool = False, model: str = BASE_MODEL,
         adapter: str = "pilot",
         out: str = "/home/keypaa/Projects/tiny-auto-classifier/reports/bench_modal.json") -> None:
    import json

    if smoke:
        print(json.dumps(smoke_batch.remote(), indent=2))
        return
    use_adapter = adapter.lower() != "none"
    # Three chunks: each result lands on local disk immediately, so a kill
    # loses at most one chunk instead of the whole run.
    merged = {"tp_deny": 0, "tn_approve": 0, "fp_waved": 0, "fn_blocked": 0,
              "lat_wsum": 0.0, "lat_n": 0}
    out_path = out
    for start, end in [(0, 1000), (1000, 2000), (2000, 3000)]:
        part = run_full_bench.remote(start, end, model, use_adapter)
        c = part["confusion"]
        for k in ("tp_deny", "tn_approve", "fp_waved", "fn_blocked"):
            merged[k] += c[k]
        merged["lat_wsum"] += part["latency_s"] * part["n"]
        merged["lat_n"] += part["n"]
        with open(out_path, "w") as fh:
            json.dump({"partial_upto": end, **part}, fh, indent=2)
        print(f"chunk {start}..{end} saved "
              f"(acc={part['accuracy']} fa={part['false_approve']})", flush=True)
    tp, tn, fp, fn = (merged["tp_deny"], merged["tn_approve"],
                      merged["fp_waved"], merged["fn_blocked"])
    n = tp + tn + fp + fn
    res = {
        "model": model + ("+pilot_t3_8192" if use_adapter else ""),
        "n": n,
        "accuracy": round((tp + tn) / n, 4),
        "balanced_acc": round(((tp / (tp + fn)) + (tn / (tn + fp))) / 2, 4),
        "false_approve": round(fp / (tp + fp), 4),
        "false_deny": round(fn / (tn + fn), 4),
        "latency_s": round(merged["lat_wsum"] / merged["lat_n"], 4),
        "confusion": {"tp_deny": tp, "tn_approve": tn,
                      "fp_waved": fp, "fn_blocked": fn},
    }
    print(json.dumps(res, indent=2))
    with open(out_path, "w") as fh:
        json.dump(res, fh, indent=2)
    print("wrote reports/bench_pilot3000.json (merged)")
