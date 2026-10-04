#!/usr/bin/env python3
"""
Stage 0 diagnostics A+B+C — one model load, answers three questions.

A. POLICY ABLATION — is the 27K policy actually used, or is it decorative?
   Same model, same samples, three policy conditions:
     full      the real policy (baseline; should reproduce ~0.876)
     compact   first --compact-chars of it (same rule text, ~1/13 the length)
     none      no policy at all, just transcript + action
   If none ~= full, the model decided from the action alone: it memorized the
   policy instead of reading it, and the "unchanged policy" constraint buys nothing.
   (This is the cheap version of the policy-faithfulness test.)

B. ERROR STRATIFICATION — where do the residual errors live?
   By category and by dynamic-suffix token length. If errors concentrate in
   long_context / large suffixes, length is implicated. If flat, data is.

C. CONFUSION + PAIR CONSISTENCY — template leakage detector.
   Category confusion matrix, plus: for minimal pairs (same prompt shape, one
   property flipped, label flipped), does the prediction actually flip?

Run on GPU (CPU is ~100s/sample at 8K). Writes raw logits so threshold and
calibration work is post-hoc and free.

Usage:
  python scripts/diag_stage0.py --adapter models/checkpoints/pilot_t3_8192 \
      --limit 300 --out reports/stage0_diag.json
"""
from __future__ import annotations
import argparse, json, sys, time
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import torch
from transformers import AutoConfig, AutoModelForSequenceClassification, AutoTokenizer

from src.prompt.builder import PromptBuilder

CATS = ["clear_allow", "clear_block", "consent_boundary", "minimal_pairs",
        "adversarial", "long_context", "rule_interaction"]


def metrics(y_true, y_pred) -> dict:
    y_true = np.asarray(y_true); y_pred = np.asarray(y_pred)
    fa_den = int((y_true == 1).sum()); fd_den = int((y_true == 0).sum())
    return {
        "n": len(y_true),
        "accuracy": round(float((y_true == y_pred).mean()), 4),
        "false_allow": round(float(((y_pred == 0) & (y_true == 1)).sum() / max(1, fa_den)), 4),
        "false_deny": round(float(((y_pred == 1) & (y_true == 0)).sum() / max(1, fd_den)), 4),
        "block_rate": round(float(y_true.mean()), 4),
    }


@torch.no_grad()
def infer(model, tok, rows, policy_text, max_length, device) -> list[dict]:
    """One forward per row with a given policy. Returns raw logits."""
    out = []
    pb = _pb_with(policy_text)
    for i, r in enumerate(rows):
        prompt, _ = pb.build(transcript=r.get("transcript", ""), metadata=r.get("metadata", ""),
                             latest_action=r.get("latest_action", ""), verify=False)
        enc = tok(prompt, truncation=True, max_length=max_length, padding=False,
                  return_attention_mask=True)
        ids = torch.tensor([enc["input_ids"]], device=device)
        msk = torch.tensor([enc["attention_mask"]], device=device)
        logits = model(input_ids=ids, attention_mask=msk).logits[0].float().cpu().numpy()
        p = torch.softmax(torch.tensor(logits), dim=-1).numpy()
        out.append({
            "sample_id": r.get("sample_id"), "category": r.get("category"),
            "label": r.get("label"), "pair_id": r.get("pair_id"),
            "logit_allow": float(logits[0]), "logit_block": float(logits[1]),
            "p_block": float(p[1]),
            "n_tokens": len(enc["input_ids"]),
        })
        if (i + 1) % 100 == 0:
            print(f"    {i+1}/{len(rows)}", flush=True)
    return out


_PB_CACHE: dict = {}


def _pb_with(policy_text: str) -> PromptBuilder:
    """PromptBuilder over an arbitrary policy string, bypassing the file/hash check.
    Keyed by content hash, not id() — id() is reused after GC and would alias variants."""
    import hashlib, tempfile
    key = hashlib.sha256(policy_text.encode("utf-8")).hexdigest()
    if key not in _PB_CACHE:
        d = Path(tempfile.mkdtemp())
        p = d / "policy.txt"
        p.write_text(policy_text, encoding="utf-8")
        _PB_CACHE[key] = PromptBuilder(p)
    return _PB_CACHE[key]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default="models/checkpoints/pilot_t3_8192")
    ap.add_argument("--data", default="data/validation/pilot_10k.jsonl")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--max-length", type=int, default=8192)
    ap.add_argument("--compact-chars", type=int, default=8000)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--out", default="reports/stage0_diag.json")
    args = ap.parse_args()

    base = json.loads(Path(args.adapter, "adapter_config.json").read_text())["base_model_name_or_path"]
    tok = AutoTokenizer.from_pretrained(base, trust_remote_code=True)
    tok.truncation_side = "left"   # same as training
    cfg = AutoConfig.from_pretrained(base, trust_remote_code=True)
    cfg.num_labels = 2
    print(f"Loading {base} + adapter {args.adapter} on {args.device}...")
    model = AutoModelForSequenceClassification.from_pretrained(
        base, config=cfg, trust_remote_code=True, dtype=torch.float16 if args.device == "cuda" else torch.float32)
    from peft import PeftModel
    model = PeftModel.from_pretrained(model, args.adapter).to(args.device).eval()

    rows = [json.loads(l) for l in Path(args.data).read_text().splitlines() if l.strip()][: args.limit]
    policy = Path("prompts/original/policy.txt").read_text(encoding="utf-8")
    variants = {
        "full": policy,
        "compact": policy[: args.compact_chars],
        "none": "",
    }
    print(f"policy chars: full={len(policy)} compact={len(variants['compact'])} none=0")

    results: dict = {"conditions": {}, "policy_chars": {k: len(v) for k, v in variants.items()}}
    preds_by_cond: dict = {}
    for name, ptext in variants.items():
        print(f"\n[A] condition '{name}' ({len(ptext)} chars)...", flush=True)
        t0 = time.perf_counter()
        pr = infer(model, tok, rows, ptext, args.max_length, args.device)
        preds_by_cond[name] = pr
        yt = [0 if r["label"] == "ALLOW" else 1 for r in pr]
        yp = [1 if p["logit_block"] > p["logit_allow"] else 0 for p in pr]
        m = metrics(yt, yp)
        m["seconds"] = round(time.perf_counter() - t0, 1)
        m["median_tokens"] = int(np.median([p["n_tokens"] for p in pr]))
        results["conditions"][name] = m
        print(f"    {json.dumps(m)}", flush=True)

    # ---- B. stratification on the baseline (full) ----
    base_pred = preds_by_cond["full"]
    yt = np.array([0 if r["label"] == "ALLOW" else 1 for r in base_pred])
    yp = np.array([1 if p["logit_block"] > p["logit_allow"] else 0 for p in base_pred])
    by_cat = {}
    for c in CATS:
        sel = [i for i, p in enumerate(base_pred) if p["category"] == c]
        if sel:
            by_cat[c] = metrics(yt[sel], yp[sel])
    lens = np.array([p["n_tokens"] for p in base_pred])
    q = np.percentile(lens, [25, 50, 75, 90])
    buckets = [("<p25", lens <= q[0]), ("p25-50", (lens > q[0]) & (lens <= q[1])),
               ("p50-75", (lens > q[1]) & (lens <= q[2])), ("p75-90", (lens > q[2]) & (lens <= q[3])),
               (">p90", lens > q[3])]
    by_len = {name: metrics(yt[m], yp[m]) for name, m in buckets if m.sum()}
    results["B_by_category"] = by_cat
    results["B_by_suffix_tokens"] = {"percentiles": {str(int(x)): int(np.percentile(lens, x)) for x in [25, 50, 75, 90, 99]}, "buckets": by_len}

    # ---- C. confusion matrix + pair consistency ----
    cats = [c for c in CATS if any(p["category"] == c for p in base_pred)]
    ci = {c: i for i, c in enumerate(cats)}
    mat = np.zeros((len(cats), 2), dtype=int)
    for i, p in enumerate(base_pred):
        if p["category"] in ci:
            mat[ci[p["category"]], 1 if p["logit_block"] > p["logit_allow"] else 0] += 1
    results["C_confusion_by_category"] = {c: {"ALLOW": int(mat[i, 0]), "BLOCK": int(mat[i, 1]),
                                              "always_BLOCK_rate": round(mat[i, 1] / max(1, mat[i].sum()), 3)}
                                          for c, i in ci.items()}
    pairs = defaultdict(dict)
    for p in base_pred:
        if p.get("pair_id"):
            pairs[p["pair_id"]][p["sample_id"]] = p
    consistent = 0; checked = 0
    for _pid, d in pairs.items():
        if len(d) == 2:
            (s1, p1), (s2, p2) = sorted(d.items())
            r1 = 1 if p1["logit_block"] > p1["logit_allow"] else 0
            r2 = 1 if p2["logit_block"] > p2["logit_allow"] else 0
            t1 = 1 if p1["label"] == "BLOCK" else 0
            t2 = 1 if p2["label"] == "BLOCK" else 0
            if t1 != t2:      # only pairs whose label actually flips
                checked += 1
                consistent += int(r1 != r2)
    results["C_pair_consistency"] = {"pairs_checked": checked, "prediction_flipped_with_label": consistent,
                                     "rate": round(consistent / checked, 4) if checked else None}

    results["reference_T3_8k_train_side"] = {"accuracy": 0.876, "false_allow": 0.0336, "false_deny": 0.2948}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2))
    Path(args.out).with_suffix(".preds.json").write_text(json.dumps(preds_by_cond))
    print("\n" + json.dumps(results, indent=2)[:4000])
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
