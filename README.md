# Tiny CPU Auto-Mode Classifier

Phase 0 — Infrastructure (from `tiny_auto_mode_classifier_roadmap.md`)

Target: smallest CPU-only model that can apply the **unchanged 20–30K-token** Auto Mode policy.

See `ROADMAP.md` for full plan (copy of original roadmap).

## Structure
```
configs/ models/training/evaluation/runtime
prompts/original + manifests/
data/raw/generated/teacher/train/validation/test/adversarial
models/base/checkpoints/exports
src/prompt_builder, tokenizer_probe, models/, dataset/, training/, evaluation/, calibration/, runtimes/, wrapper/
experiments/ reports/ scripts/
```

## Phase 0 Artifacts
- `src/prompt/` — PromptBuilder (canonical replay, SHA-256)
- `src/prompt/tokenizer_probe.py` — token counts per tokenizer
- `src/evaluation/schema.py` — benchmark schema
- `src/runtimes/cpu_timer.py` — p50/p95/RSS timer
- `configs/hardware.json` — recorded hardware

## Fail-closed
If `policy_hash` mismatches manifest: `ABORT BENCHMARK` (raise `PolicyHashMismatchError`).

