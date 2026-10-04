#!/usr/bin/env python3
"""
T0 baseline — the floor. Can a bag-of-chunks + logistic regression do this task?

No attention across chunks. No fine-tuning. No policy comprehension.
If this approaches our T3 score, the whole architecture discussion is moot.

Pipeline:
  27K-token prompt -> N chunks of --chunk-tokens -> frozen embedder -> 1 vector
  per chunk -> mean pool -> ONE 768-d vector per sample -> logistic regression.

Embeds are cached to .npy so the (expensive) pass runs once; re-running the
regression is then free and we can compare pooling strategies cheaply.

Usage (colab T4):
  python scripts/t0_baseline.py --limit-train 8000 --out reports/t0_baseline.json
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

from src.prompt.builder import PromptBuilder

EMB_PATH = "data/t0_emb.npz"


def load_rows(path: str, limit: int = 0) -> list[dict]:
    rows = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
    return rows[:limit] if limit else rows


def build_states(rows: list[dict], policy_path: str) -> list[str]:
    """Exact same prompt the SFT sees (hash-checked), so T0 vs T3 is apples-to-apples."""
    pb = PromptBuilder(policy_path)
    pb.verify_policy()
    out = []
    for r in rows:
        s, _ = pb.build(
            transcript=r.get("transcript", ""),
            metadata=r.get("metadata", ""),
            latest_action=r.get("latest_action", ""),
            verify=False,
        )
        out.append(s)
    return out


def pool_chunks(vecs: np.ndarray, owner: np.ndarray, n: int) -> np.ndarray:
    """Mean-pool per-sample across its chunks, then L2-normalize. Bag of chunks by design."""
    dim = vecs.shape[1]
    X = np.zeros((n, dim), dtype=np.float32)
    for i in range(n):
        sel = vecs[owner == i]
        X[i] = sel.mean(0) if len(sel) else 0.0
    return X / np.linalg.norm(X, axis=1, keepdims=True).clip(min=1e-6)


@torch.no_grad()
def embed_states(rows: list[dict], policy_path: str, model_id: str, chunk_tokens: int,
                 batch: int, device: str, include_policy: bool = False):
    """
    Chunk by token IDs and feed IDs straight to the model.

    Measured bottlenecks this avoids (scripts/bench_embed.py):
      1. decode/re-tokenize per chunk (lossy: BPE merges change)
      2. fp32 on T4: bf16 is EMULATED on Turing and is 1.7x SLOWER than fp32
         (350 vs 202 ms/chunk) — measured. fp16 has native tensor cores.
      3. chunk length 512 beats 1024 (86 vs 202 ms/chunk)

    include_policy=False by default, and that is the whole point:
      The policy is byte-identical for all rows, so for a bag-of-chunks model it
      contributes the SAME vector c to every sample. A constant carries zero
      discriminative signal and is absorbed by the logistic-regression intercept.
      Embedding it 9000x is 198k chunks of pure noise. We embed only the dynamic
      suffix (transcript + action), which is where all the label information is.
    """
    tok = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    model = AutoModel.from_pretrained(model_id, trust_remote_code=True).to(device).eval()
    # T4 = Turing: native fp16 tensor cores, EMULATED bf16 (slower). fp16 it is.
    if device.startswith("cuda"):
        model = model.half()
        print("  model dtype: float16 (native on T4; bf16 is emulated and slower)", flush=True)
    pad_id = tok.pad_token_id if tok.pad_token_id is not None else 0

    pb = PromptBuilder(policy_path)
    pb.verify_policy()

    policy_ids: list[int] = []
    if include_policy:
        t0 = time.perf_counter()
        policy_ids = tok(pb.policy_text, add_special_tokens=False, truncation=True,
                         max_length=32768)["input_ids"]
        print(f"  + policy tokenized once: {len(policy_ids)} tokens ({time.perf_counter()-t0:.1f}s)", flush=True)

    chunk_ids: list[list[int]] = []
    owner: list[int] = []
    t0 = time.perf_counter()
    for i, r in enumerate(rows):
        dyn = pb.dynamic_template.format(
            transcript=r.get("transcript", ""),
            metadata=r.get("metadata", ""),
            latest_action=r.get("latest_action", ""),
        )
        dyn_ids = tok(dyn, add_special_tokens=False, truncation=True, max_length=4096)["input_ids"]
        ids = policy_ids + dyn_ids
        if not ids:
            ids = [tok.eos_token_id or 0]
        for j in range(0, len(ids), chunk_tokens):
            chunk_ids.append(ids[j : j + chunk_tokens])
            owner.append(i)
        if (i + 1) % 3000 == 0:
            print(f"  tokenized {i+1}/{len(rows)} states ({time.perf_counter()-t0:.0f}s)", flush=True)
    owner = np.array(owner)
    n_chunks = len(chunk_ids)
    print(f"  {len(rows)} states -> {n_chunks} chunks of {chunk_tokens} tokens"
          f"{' (policy EXCLUDED)' if not include_policy else ''}", flush=True)

    vecs = np.zeros((n_chunks, model.config.hidden_size), dtype=np.float32)
    t0 = time.perf_counter()
    for s in range(0, n_chunks, batch):
        ids_batch = chunk_ids[s : s + batch]
        L = max(len(c) for c in ids_batch)
        # vectorized: pad into one numpy block, no per-row torch.tensor()
        buf = np.full((len(ids_batch), L), pad_id, dtype=np.int64)
        mbuf = np.zeros((len(ids_batch), L), dtype=np.int64)
        for k, c in enumerate(ids_batch):
            buf[k, : len(c)] = c
            mbuf[k, : len(c)] = 1
        inp = torch.from_numpy(buf).to(device)
        msk = torch.from_numpy(mbuf).to(device)
        h = model(input_ids=inp, attention_mask=msk).last_hidden_state
        m = msk.unsqueeze(-1).to(h.dtype)
        pooled = (h * m).sum(1) / m.sum(1).clamp(min=1e-6)
        vecs[s : s + batch] = torch.nn.functional.normalize(pooled.float(), dim=-1).cpu().numpy()
        if (s // batch) % 50 == 0:
            done = min(s + batch, n_chunks)
            rate = done / max(1e-9, time.perf_counter() - t0)
            eta = (n_chunks - done) / max(1e-9, rate)
            print(f"  chunk {done}/{n_chunks}  {rate:.0f}/s  eta {eta/60:.1f}min", flush=True)

    # mean-pool per sample across its chunks
    X = pool_chunks(vecs, owner, len(states))
    return X


def metrics(y_true, y_pred) -> dict:
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    acc = float((y_true == y_pred).mean())
    fa_den = (y_true == 1).sum()
    fd_den = (y_true == 0).sum()
    fa = float(((y_pred == 0) & (y_true == 1)).sum() / max(1, fa_den))
    fd = float(((y_pred == 1) & (y_true == 0)).sum() / max(1, fd_den))
    return {"accuracy": round(acc, 4), "false_allow": round(fa, 4), "false_deny": round(fd, 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedder", default="nomic-ai/modernbert-embed-base")
    ap.add_argument("--chunk-tokens", type=int, default=512)  # measured: 86ms vs 202ms at 1024
    ap.add_argument("--batch", type=int, default=32)  # measured flat past 16
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--limit-train", type=int, default=8000)
    ap.add_argument("--limit-eval", type=int, default=1000)
    ap.add_argument("--cache", default=EMB_PATH)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--include-policy", action="store_true", help="also embed the policy (constant across rows; off by default)")
    ap.add_argument("--out", default="reports/t0_baseline.json")
    args = ap.parse_args()

    tr = load_rows("data/train/pilot_10k.jsonl", args.limit_train)
    ev = load_rows("data/validation/pilot_10k.jsonl", args.limit_eval)
    y_tr = np.array([0 if r["label"] == "ALLOW" else 1 for r in tr])
    y_ev = np.array([0 if r["label"] == "ALLOW" else 1 for r in ev])

    cache = Path(args.cache)
    if cache.exists() and not args.no_cache:
        d = np.load(cache, allow_pickle=True)
        X_tr, y_tr, X_ev, y_ev = d["X_tr"], d["y_tr"], d["X_ev"], d["y_ev"]
        print(f"loaded cached embeddings {X_tr.shape} / {X_ev.shape}")
    else:
        print(f"Embedding {len(tr)+len(ev)} states on {args.device} ({args.embedder})...")
        t0 = time.perf_counter()
        X_all = embed_states(tr + ev, "prompts/original/policy.txt",
                             args.embedder, args.chunk_tokens, args.batch, args.device, args.include_policy)
        X_tr, X_ev = X_all[: len(tr)], X_all[len(tr):]
        y_tr, y_ev = y_tr, y_ev
        mins = (time.perf_counter() - t0) / 60
        print(f"  embedded in {mins:.1f} min")
        if not args.no_cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(cache, X_tr=X_tr, y_tr=y_tr, X_ev=X_ev, y_ev=y_ev)
            print(f"  cached -> {cache}")

    from sklearn.linear_model import LogisticRegression
    from sklearn.svm import LinearSVC

    out: dict = {
        "embedder": args.embedder, "chunk_tokens": args.chunk_tokens,
        "n_train": int(len(y_tr)), "n_eval": int(len(y_ev)),
        "val_block_rate": round(float(y_ev.mean()), 4),
        "results": {},
    }
    for name, clf in [
        ("logreg_C1", LogisticRegression(max_iter=2000, C=1.0)),
        ("logreg_C10", LogisticRegression(max_iter=2000, C=10.0)),
        ("logreg_classbalanced", LogisticRegression(max_iter=2000, C=1.0, class_weight="balanced")),
        ("linearsvc", LinearSVC(dual="auto", max_iter=3000)),
    ]:
        clf.fit(X_tr, y_tr)
        m = metrics(y_ev, clf.predict(X_ev))
        m["train_acc"] = round(float(clf.score(X_tr, y_tr)), 4)
        out["results"][name] = m
        print(f"{name:24s} {m}")

    best = min(out["results"].items(), key=lambda kv: (kv[1]["false_allow"], -kv[1]["accuracy"]))
    out["best_by_false_allow"] = best[0]
    out["reference_T3_8k"] = {"accuracy": 0.876, "false_allow": 0.0336, "false_deny": 0.2948}

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2))
    print("\n" + json.dumps(out, indent=2))
    print(f"\nWrote {args.out}")
    ba = out["results"][best[0]]
    print("\nInterpretation:")
    print(f"  T0 best: accuracy {ba['accuracy']} / false_allow {ba['false_allow']} / false_deny {ba['false_deny']}")
    print( "  T0 within ~0.02 of T3 (0.876) => long-context attention is not earning its cost, rethink")
    print( "  T0 far below T3 => the 395M long-context encoder IS doing real work, project justified")


if __name__ == "__main__":
    main()
