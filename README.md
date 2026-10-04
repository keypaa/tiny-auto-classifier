# Tiny CPU Auto-Mode Classifier

Distil one expensive decision — *does this agent action violate the Auto Mode policy?* — into the smallest CPU-only model that can say `ALLOW` or `BLOCK + rule`, and fall back when unsure.

Not a tiny Claude. A security classifier. Full plan in `ROADMAP.md`.

---

## Status: where we actually are

| Phase | State |
|---|---|
| Infra (PromptBuilder, hash guard, eval schema, CPU timer, wrapper) | done |
| Model survey | done — `reports/model_survey.md` |
| `auto-0.4b` reproduction | done — `reports/auto-0.4b_reproduction.md` |
| Pilot SFT (T3 encoder, 8K) | done — `0.876 / 0.029 FA / 0.302 FD` on our synthetic val |
| **Stage 0 diagnostics** | **done — the core hypothesis failed its own test** |
| Next decision | in-context policy application vs learned rule |

### The Stage 0 result, in one line

Our fine-tuned encoder scores **0.883 with the full 27K policy in the input and 0.873 with no policy at all**. It is not applying the policy in-context — it learned a rule. Full analysis: `reports/stage0_findings.md`.

Along the way `ProCreations` published `auto-1b` (96.40% accuracy, 3.19% false-deny, 97.02% long-context) plus an ONNX int8 export, and two datasets (`ProCreations/auto` 700k, `ProCreations/approve-or-deny` 3k). Our numbers and theirs are **not comparable** — different datasets — and that is the current blocker.

---

## Layout

```
configs/models/         survey specs per candidate
configs/training/       pilot.yaml (8K) + pilot_16k / pilot_27k / t1 / t4
configs/hardware.json   target machine: Ryzen 4500U, 6c, 21GB DDR4
prompts/original/       policy.txt + manifests/policy.sha256  (SYNTHETIC placeholder)
src/prompt/builder.py   canonical replay + SHA-256 fail-closed guard
src/training/           config.py | dataset.py | sft.py   (encoder + decoder SFT, LoRA)
src/dataset/            pilot_generator.py | dpo_pairs.py
src/evaluation/schema.py BaselineRow / FailureRecord
src/runtimes/cpu_timer.py p50/p95/p99 + RSS
src/wrapper/deterministic.py  {decision,rule_id,confidence} -> XML, fail-closed
scripts/                check_policy | train | eval | diag_stage0 | diag_onnx | colab_setup
reports/                survey, repro, run results, stage0 findings
docs/AGENT_BRIEF.md     self-contained brief for an independent agent to re-plan
```

## Hard constraints (unchanged)

- **Inference is CPU-only.** Target: 6-core Ryzen 4500U, AVX2, 21GB DDR4, batch 1.
- **The policy must not be summarized, compressed, retrieved piecewise or rewritten.** Primary benchmark constraint.
- **Output is deterministic and structured.** Must carry a rule id. Fail closed on anything unexpected.

## Fail-closed

`PolicyHashMismatchError` on policy hash drift; `src/wrapper/deterministic.py` returns `FALLBACK` for unknown rule, NaN/Inf confidence, malformed output or out-of-range confidence. Never silently allows.

## Quickstart

```bash
pip install -e ".[dev]"          # or: pip install transformers peft scikit-learn pyyaml psutil

python scripts/check_policy.py   # SHA-256 guard (fail-closed)

# diagnostics
python scripts/diag_stage0.py --adapter <adapter_dir> --limit 300   # needs GPU
python scripts/diag_onnx.py --skip-fp32-torch                      # CPU latency + INT8

# training (Colab T4 or better)
bash scripts/colab_setup.sh
python scripts/train.py --config configs/training/pilot.yaml --max-length 8192 \
    --output-dir models/checkpoints/pilot_t3_8192
python scripts/train.py --config configs/training/pilot_16k.yaml \
    --resume models/checkpoints/pilot_t3_8192
```

`scripts/molab_ssh_up.sh` is kept for reference only — **SSH tunnelling violates Molab's terms of use** and got this account restricted. Do not run it on Molab.

## Conventions

- **Accuracy on GPU, latency on CPU.** They are different questions; never mix them.
- **bf16 is native on Blackwell/A100, emulated on Turing (T4)** where it is 1.7× slower than fp32. `sft.py` picks per device.
- **Truncate on the left.** The policy is a constant prefix and the decision signal is the suffix; right-truncation silently feeds identical policy prefixes and the model learns only the class prior.
- **Every experiment writes a report under `reports/`** with measured numbers, never hand-copied.
