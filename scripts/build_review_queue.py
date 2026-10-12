#!/usr/bin/env python3
"""Assemble the hand-review queue: 200 unique (transcript, action) pairs.

Composition (quality-first: every row earns its place):
  - all oracle rows (dots-3 verdicts with vote margins) — highest signal
  - organic allows, deduplicated and spread across tools — the allow baseline
  - corpus-asserted rows (rule-derived direction) spread across distinct rules
  - corpus adversarial/injection rows — the attack surface
  - corpus boundary rows (signals present, direction null) — genuine ambiguity

Dedup by sha1(transcript + action) so repeated organic actions (curl ×10)
are reviewed once. Deterministic under --seed.

Usage:
    python3 scripts/build_review_queue.py
    python3 scripts/build_review_queue.py --n 200 --seed 7 --out data/review_queue.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TARGETS = {
    "oracle": None,      # all oracle rows, whatever the count
    "organic_allow": 45,
    "corpus_asserted": 60,
    "corpus_adversarial": 30,
    "corpus_boundary": 30,
}


def key_of(transcript: str, action: str) -> str:
    return hashlib.sha1((transcript + "\x00" + action).encode()).hexdigest()[:16]


def load_oracle() -> list[dict]:
    rows = []
    for path in sorted((ROOT / "data").glob("labels_*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_capture() -> list[dict]:
    recs = []
    for path in sorted((ROOT / "data" / "capture").rglob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                recs.append(json.loads(line))
    return recs


def load_corpus() -> list[dict]:
    path = ROOT / "samples" / "corpus-10k.json"
    if not path.exists():
        return []
    d = json.loads(path.read_text())
    return d["corpus"] + d.get("fail_open", [])


def oracle_row(r: dict) -> dict:
    votes = r.get("vote_split")
    margin = ""
    if votes:
        margin = f"{votes.get('block', 0)}-{votes.get('allow', 0)}"
    return {
        "source": "oracle",
        "ref_id": r.get("id"),
        "transcript": r.get("transcript", ""),
        "action": r.get("action", ""),
        "prefill_block": r.get("block"),
        "prefill_category": r.get("category_slug"),
        "prefill_note": (
            f"oracle {r.get('model')} vote {margin or 'single'}; "
            f"expected {r.get('rule_expect')}"
        ),
    }


def capture_row(r: dict) -> dict:
    fin = r.get("final", {})
    blocks = [b.get("text", "") for b in r.get("transcript_blocks", [])]
    transcript = "".join(blocks) if blocks else ""
    return {
        "source": "organic",
        "ref_id": f"{r.get('ts', '')[:10]}/{r.get('session_id', '')[:8]}",
        "transcript": transcript,
        "action": r.get("action_projection", ""),
        "prefill_block": bool(fin.get("should_block")),
        "prefill_category": fin.get("category_slug"),
        "prefill_note": (
            f"observed {r.get('classifier_model')} "
            f"src={fin.get('decision_source') or r.get('decision_source')}"
        ),
    }


def corpus_row(r: dict, why: str) -> dict:
    tags = r.get("tags") or []
    return {
        "source": f"corpus-{why}",
        "ref_id": r.get("id"),
        "transcript": r.get("transcript", ""),
        "action": r.get("action_projected", ""),
        "prefill_block": {"block": True, "allow": False}.get(
            r.get("expected_block_direction") or "", None),
        "prefill_category": None,
        "prefill_note": (
            f"rule-derived {r.get('expected_block_direction')}; "
            f"tags={','.join(tags[:3])}; ctx={r.get('context_kind')}"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "review_queue.jsonl")
    args = ap.parse_args()
    rng = random.Random(args.seed)

    queue: list[dict] = []
    seen: set[str] = set()

    def add(row: dict) -> bool:
        k = key_of(row["transcript"], row["action"])
        if not row["transcript"] or not row["action"] or k in seen:
            return False
        seen.add(k)
        row["qid"] = f"q{len(queue)+1:03d}"
        queue.append(row)
        return True

    oracle = load_oracle()
    for r in oracle:
        add(oracle_row(r))
    print(f"oracle: {len([q for q in queue if q['source']=='oracle'])} rows")

    cap = [r for r in load_capture() if not r.get("final", {}).get("should_block")]
    rng.shuffle(cap)
    by_tool: Counter = Counter()
    n_org = 0
    for r in cap:
        if n_org >= TARGETS["organic_allow"]:
            break
        if by_tool[r.get("tool_name")] >= 25:
            continue
        if add(capture_row(r)):
            by_tool[r.get("tool_name")] += 1
            n_org += 1
    print(f"organic_allow: {n_org} rows")

    corpus = load_corpus()
    asserted = [r for r in corpus
                if r.get("label_confidence") == "rule-derived"
                and r.get("expected_block_direction")]
    by_tag: Counter = Counter()
    rng.shuffle(asserted)
    n_ass = 0
    for r in asserted:
        if n_ass >= TARGETS["corpus_asserted"]:
            break
        tag = (r.get("tags") or ["?"])[0]
        if by_tag[tag] >= 5:
            continue
        if add(corpus_row(r, "asserted")):
            by_tag[tag] += 1
            n_ass += 1
    print(f"corpus_asserted: {n_ass} rows across {len(by_tag)} rules")

    adv_kinds = {"injection-transcript", "injection-forwarded-user-turns",
                 "injection-mixed", "tool-output-injection"}
    adv = [r for r in corpus if r.get("context_kind") in adv_kinds]
    rng.shuffle(adv)
    n_adv = 0
    for r in adv:
        if n_adv >= TARGETS["corpus_adversarial"]:
            break
        if add(corpus_row(r, "adversarial")):
            n_adv += 1
    print(f"corpus_adversarial: {n_adv} rows")

    boundary = [r for r in corpus
                if r.get("expected_block_direction") is None
                and r.get("signals") and not r.get("is_ext")]
    rng.shuffle(boundary)
    n_bnd = 0
    for r in boundary:
        if n_bnd >= TARGETS["corpus_boundary"]:
            break
        if add(corpus_row(r, "boundary")):
            n_bnd += 1
    print(f"corpus_boundary: {n_bnd} rows")

    # Top-up: fill remaining slots from the corpus pool in priority order
    # (asserted rules first, then anything unseen) so the queue always
    # reaches exactly n without duplicating.
    if len(queue) < args.n:
        rest = [r for r in asserted + boundary + adv if True]
        rng.shuffle(rest)
        for r in rest:
            if len(queue) >= args.n:
                break
            why = ("asserted" if r.get("expected_block_direction")
                   else "boundary" if r.get("signals") else "adversarial")
            add(corpus_row(r, f"topup-{why}"))
        print(f"top-up: {len(queue)} rows total")

    queue = queue[: args.n]
    for i, q in enumerate(queue, 1):
        q["qid"] = f"q{i:03d}"
    args.out.write_text(
        "\n".join(json.dumps(q, ensure_ascii=False) for q in queue) + "\n",
        encoding="utf-8")
    print(f"\nwrote {args.out}: {len(queue)} rows")
    print("sources:", dict(Counter(q["source"] for q in queue)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())