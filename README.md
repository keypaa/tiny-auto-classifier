# Tiny CPU Auto-Mode Classifier

**One-line mission:** distill one expensive decision — *does this agent action violate the unchanged 20–30K-token Auto Mode policy?* — into the smallest CPU-only model that can say `ALLOW` or `BLOCK + exact rule` and fall back when unsure.

We are not building a tiny Claude. We are building a security classifier (`ROADMAP.md:1`).

---

## What we are doing (phases)

```
Phase 0  Infrastructure          — done: PromptBuilder (SHA-256), tokenizer probe, benchmark schema, CPU timer, wrapper, hardware 6c Ryzen 4500U 21GB
Phase 1  Model survey            — done: T1 230M LFM, T2 270M Gemma (32K), T3 395M auto-0.4b (64K sparse, sweet spot), T4 0.6B Qwen (32K)
Phase 2  auto-0.4b + baseline    — done: 2-label approve/deny, RoPE 2.56M, ONNX trap ~8K / PT+flash_attn 64K, dry-run matrix 8K→64K
Phase 3  Dataset                 — stub only: 750 toy minimal pairs (data/raw/pilot_stub.jsonl) — NOT real training data
Phase 4  Pilot SFT 10–25K        — scaffold ready for Colab T4 12GB (LoRA r16, grad checkpointing, fp16, curriculum 8K→16K→27K) — DO NOT run on stub for real
Phase 5+ Full SFT / DPO / RL / Quant / CPU runtime / Calibration / Cascade
```

**T-ladder = size:** `T1 230M → T2 270M → T3 395M → T4 0.6B`. **L-ladder = length:** `8K →16K →27K` (full policy ≈27K tokens ≈108K chars). We train short then long (curriculum) because `27K` is `3.5×` the work and OOMs without `flash_attn`.

**Current dataset = stub** (`data/README.md:1`). Real pilot needs `§11` categories (hard/soft block, consent, provenance, scope, wrappers, injection), balance `20/20/20/15/10/10/5`, minimal pairs flipping one property, `6` depth placements (`0-10% … 90-100%`), teacher A/B labeling, explicit holdouts (`RULE_HOLDOUT` etc) in `data/train/validation/test/adversarial/`.

---

## Repo structure

```
configs/models/          model survey JSONs
configs/training/        pilot.yaml (T3), pilot_t1.yaml, pilot_t4.yaml
configs/hardware.json    Ryzen 4500U DDR4 — inference target
prompts/original/policy.txt + manifests/policy.sha256  — SHA-256 verified, mismatch = ABORT BENCHMARK
src/prompt/builder.py    canonical replay: policy + transcript + metadata + latest_action → prompt + manifest
src/prompt/tokenizer_probe.py
src/evaluation/schema.py BaselineRow / FailureRecord (§35)
src/runtimes/cpu_timer.py p50/p95/p99 + RSS (§28 split: tokenization / attention / head / wrapper)
src/wrapper/deterministic.py  {decision,rule_id,confidence} → <block>no</block> / <block>yes</block><category>
src/training/config.py | dataset.py | sft.py   lean SFT, encoder vs decoder, LoRA+checkpointing
scripts/check_policy.py, build_prompt.py, probe_tokenizers.py, run_baseline_matrix.py, train.py, colab_setup.sh
reports/   model_survey.md / auto-0.4b_reproduction.md / baseline_matrix.md (versioned)
data/raw/pilot_stub.jsonl  — 750 toy rows, plumbing only
```

**Fail-closed `src/wrapper/deterministic.py:1`:** any of `policy_hash mismatch, truncation, unknown rule, NaN confidence, timeout, runtime error` → `FALLBACK`, never `ALLOW`.

---

## Quickstart

### 1) Local (Ryzen CPU, no GPU) — verify infra

```bash
git clone <this>
cd tiny-auto-classifier
python scripts/check_policy.py          # OK policy_hash=e091... 108313 chars (synthetic placeholder — replace with real policy)
python scripts/build_prompt.py --latest-action "rm -rf /tmp/foo" --metadata "test" --transcript "User: hi"
python scripts/probe_tokenizers.py      # optional
python scripts/run_baseline_matrix.py --dry-run   # proves schema, writes reports/baseline_matrix.md
python -m src.training.dataset          # or: python src/dataset/generator.py --n 500
```

### 2) Colab T4 12GB VRAM / 15GB RAM / 4 cores — when real dataset is ready

```bash
# one-time setup (no training)
bash scripts/colab_setup.sh
# → installs transformers peft accelerate bitsandbytes
# → verifies policy hash
# → dry-run one batch per tier (checks no OOM, prints CUDA free)

# DO NOT run the loop on the stub — stub is not real data.
# After you have data/train/pilot_10k.jsonl (real §11 data):

# cheapest proof: 8K only (~4h for T3 on T4 30%)
python scripts/train.py --config configs/training/pilot.yaml --max-length 8192 --output-dir models/checkpoints/pilot_t3_8192

# then curriculum (when 8K learns):
for L in 8192 16384 27110; do
  python scripts/train.py --config configs/training/pilot.yaml --max-length $L --output-dir models/checkpoints/pilot_t3_$L
done
# same for T1/T4: --config configs/training/pilot_t1.yaml etc

# eval stays on CPU for real latency:
python scripts/run_baseline_matrix.py --contexts 8192 16384 27110 32768
```

**What not to do:** `25K@27K` straight on stub = `~19h` T4 wasted, `CPU ~300 days`. Pilot stub is for `scripts/train.py --dry-run` only (one batch).

---

## Expected costs on T4 (TFLOP = 10¹² ops count, TFLOPS = ops/s)

`FLOPs/sample = 8×N×L + 3×Attn` (`8` = recompute with checkpointing). `T4 peak 65 TFLOPS fp16, eff 19.5 @30%`

| model | 8K/sample | 27K/sample | 10K@8K | 10K@27K | 10K curriculum 8K/16K/27K avg |
|-------|-----------|------------|--------|---------|-------------------------------|
| T1 230M | 20.8 TFLOP | 113 TFLOP | 3.0h | 16.1h | 8.9h |
| T3 395M | 30.2 TFLOP | 131 TFLOP | 4.3h | 18.7h | 10.9h |
| T4 0.6B | 50.9 TFLOP | 257 TFLOP | 7.3h | 36.6h | 20.5h |

CPU Ryzen `~50 GFLOPS` → `10K@27K` `~300 days` (`390×` slower) — why training needs T4, inference needs CPU.

---

## Next step

We are at the **dataset gate**. Next commit should be real `data/train/pilot_10k.jsonl` + `validation/test/adversarial` + holdouts, then pilot SFT `8K` → threshold calibration. Training on the stub is a pipeline test, not a model.
