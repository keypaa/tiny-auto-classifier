"""
DPO preference pairs — Roadmap §22. CPU-only, deterministic.

For each pilot row emits (prompt-fields, chosen, rejected) where rejected is a
*subtly incorrect* prediction of a named confusion type:
  consent confusion | provenance confusion | scope escalation | rule confusion |
  hard/soft confusion | latest-action confusion

Chosen uses decoder target format ("ALLOW" / "BLOCK:R17") so pairs work for
causal DPO directly; encoder SFT ignores the strings and uses labels.
"""
from __future__ import annotations
import json, random
from pathlib import Path

random.seed(42)

def target_of(label: str, rule_id: str | None) -> str:
    return "ALLOW" if label == "ALLOW" else f"BLOCK:{rule_id or 'R17'}"

def rejected_variants(rec: dict) -> list[tuple[str, str]]:
    """Return [(rejected_target, confusion_type)] — never equal to chosen."""
    label, rule = rec.get("label", "ALLOW"), rec.get("rule_id") or "R17"
    chosen = target_of(label, rule)
    cands: list[tuple[str, str]] = []
    if label == "BLOCK":
        cands.append(("ALLOW", "consent confusion"))  # model reads vague yes as explicit
        cands.append((f"BLOCK:{'R01' if rule != 'R01' else 'R02'}", "rule confusion"))
        cands.append(("ALLOW", "provenance confusion"))
        cands.append(("ALLOW", "scope escalation"))
    else:
        cands.append((f"BLOCK:{rule}", "hard/soft confusion"))  # harmless read as dangerous
        cands.append((f"BLOCK:{'R01' if rule != 'R01' else 'R02'}", "rule confusion"))
        cands.append((f"BLOCK:{rule}", "latest-action confusion"))
    return [(t, c) for t, c in cands if t != chosen]

def generate(src: str = "data/train/pilot_10k.jsonl", out: str = "data/train/dpo_pairs.jsonl",
             per_row: int = 2) -> dict:
    rows = [json.loads(l) for l in Path(src).read_text().splitlines() if l.strip()]
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(out, "w") as f:
        for r in rows:
            chosen = target_of(r.get("label", "ALLOW"), r.get("rule_id"))
            for rej, conf in rejected_variants(r)[:per_row]:
                f.write(json.dumps({
                    "sample_id": f"dpo_{r['sample_id']}_{conf.split()[0]}",
                    "transcript": r.get("transcript", ""),
                    "metadata": r.get("metadata", ""),
                    "latest_action": r.get("latest_action", ""),
                    "chosen": chosen,
                    "rejected": rej,
                    "confusion": conf,
                    "category": r.get("category"),
                }) + "\n")
                n += 1
    print(f"Wrote {out}: {n} pairs from {len(rows)} rows")
    return {"pairs": n, "rows": len(rows)}

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/train/pilot_10k.jsonl")
    ap.add_argument("--out", default="data/train/dpo_pairs.jsonl")
    ap.add_argument("--per-row", type=int, default=2)
    args = ap.parse_args()
    generate(args.src, args.out, args.per_row)
