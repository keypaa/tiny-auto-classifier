# approve-or-deny benchmark — our results

*Benchmark: `ProCreations/approve-or-deny`, 3,000 rows (1,401 deny / 1,599 approve),
pinned revision `a38b6259`. Excluded from both ProCreations training corpora by
content hash. Labels model-generated (DeepSeek V4 Flash) and independently audited.
This is the only apples-to-apples eval in the project: every model below ran the
same rows under the same protocol.*

Protocol: decision threshold 0.5, deny is the positive class. Columns match
their public leaderboard. `false_approve` (dangerous call waved through) is the
metric that matters; `false_deny` (legitimate work blocked) costs one click.

## Results

| Model | n | Accuracy | False-approve | False-deny | Latency |
|---|---|---:|---:|---:|---|
| GPT-5.6-Luna (frontier ref) | 3000 | 0.9697 | 0.0493 | 0.0138 | 3.09 s |
| ProCreations/auto-1b (1B) | 3000 | 0.9640 | 0.0407 | 0.0319 | 0.010 s |
| **ProCreations/auto-0.4b-2 (verified here)** | 3000 | **0.9703** | **0.0250** | **0.0338** | 0.010 s |
| ProCreations/auto-0.4b (prior gen) | 3000 | 0.9013 | 0.0871 | 0.1088 | 0.007 s |
| **Ours: auto-0.4b + pilot_t3_8192 LoRA** | 3000 | **0.8137** | **0.0214** | **0.3308** | CPU-bound |
| regex / keyword heuristic | 3000 | 0.5797 | 0.5939 | 0.2683 | ~0 ms |
| ProCreations/auto-200m-2 (150M, verified here) | 3000 | **0.9620** | **0.0400** | **0.0363** | 0.006 s |

Frontier/1B/0.4B/regex rows are their published leaderboard figures, not our
measurements. The two bold rows are measured here (files below).

## Reading

- **Strictest gate on the board, lowest accuracy.** Our pilot waves through 30
  of 1,401 dangerous calls (2.14%) — half the rate of anything above it — but
  refuses 529 of 1,599 legitimate ones. The entire accuracy gap is over-blocking.
- **-2 reproduces its card to the decimal** (97.03% measured vs 97.00%
  claimed; 35 waved, 54 blocked-legit). It sits above the 1B model on accuracy
  with nearly half its false-approves, which makes the 400M class the ceiling
  reference and frames the open question as whether the 150M gets close enough
  to justify staying small.
- **100-row probe validated.** It predicted 2.04% false-approve; the full run
  measured 2.14%. Small-sample methodology holds for this bench.

## Method

- Harness: `scripts/eval_bench.py` (CPU) and `modal_eval.py` (Modal L4,
  token-budget bucketing to 64K, 3×1000 chunks merged). Same metric code path.
- Precision: checkpoints are native BF16; GPU runs cast to fp16 on load (T4
  has no BF16 support; L4 run kept the fp16 cast for comparability). CPU runs
  are fp32. The cast is not expected to move accuracy; a BF16-native serving
  run belongs in the deployment eval, not here.
- Pilot full run replicated **bit-identically** on T4 and L4 (confusion
  1371/1070/30/529 both cards): deterministic inference confirmed.
- -2 run: zero-shot, no adapter, fp16, L4. Their card values were not used as
  inputs anywhere; reproduction is independent.
- 100-row probes use seed 7, stratified by label × difficulty.

## Files

- `reports/bench_pilot100.json` — pilot, n=100 (CPU)
- `reports/bench_pilot3000_t4.json`, `reports/bench_pilot3000_l4.json` — pilot,
  n=3000, identical
- `reports/bench_04b2_3000.json` — auto-0.4b-2 zero-shot, n=3000 (L4)
- `reports/bench_200m2_3000.json` — auto-200m-2 zero-shot, n=3000 (L4).
  Card claimed 96.33%; measured 96.20% with FA 4.00% / FD 3.63% at 5.6 ms/call —
  half the latency of -2 for 0.8 points of accuracy.
- `data/benchmark/approve-or-deny/` — the benchmark itself (26 MB, git-tracked)
