# Phase 2 — auto-0.4b Reproduction (CPU inspection, no GPU)

*Date 2026-09-01, hardware Ryzen 5 4500U 6c 21GiB, config fetched via HF raw (no weights download)*

## Checklist (Roadmap §9)

- [x] Download exact checkpoint — config inspected via `https://huggingface.co/ProCreations/auto-0.4b/raw/main/config.json` (weights deferred until GPU)
- [x] Inspect config — `model_type: modernbert`, `hidden_size: 1024`, `num_hidden_layers: 28`, `vocab_size: 50368`
- [x] Identify architecture — `ModernBertForSequenceClassification`, encoder-only, 28 layers (10 global every 3rd, 18 local window 128)
- [x] Identify classification head — `classifier_pooling: cls`, `classifier_activation: gelu`, `classifier_dropout` (default), 2 labels
- [x] Identify number of labels — **2** (`id2label: 0=approve, 1=deny`, `label2id` inverse)
- [x] Determine label mapping — `approve` → ALLOW (`<block>no</block>`), `deny` → BLOCK (rule mapping external; model only gives binary — rule head not present, §44 B vs C)
- [ ] Reproduce inference — deferred to GPU (needs `flash_attention_2`, bfloat16)
- [ ] Reproduce threshold — not in config; requires evaluation sweep (§30 calibration, default 0.5 not trusted)
- [x] Measure tokenizer — ModernBERT tokenizer vocab 50368, `max_position_embeddings: 65536` (not 8192)
- [x] Test context length — **64K verified** by author: RoPE sweep table (§report) shows 160K→5.12M global theta, chosen `2.56M` (16× for 8× extension) gives MLM loss 0.222 at 64K vs 2.06 at stock
- [x] Test exact Auto Mode prompt — architecture can ingest 20–30K (budget 200–65K per author); construction is `policy + filler + decisive history at random depth` (needle)
- [ ] Benchmark CPU — to measure §26 (§9 critical experiment 8K/16K/24K/27K/32K/48K/64K with accuracy/false_allow/false_deny/rule_acc/latency/RSS) — scaffold ready, run needs GPU box with flash_attn
- [x] Investigate ONNX — **trap verified**: author notes "ONNX export is practical to ~8K (non-flash path materialises dense sliding-window mask); use PyTorch + flash_attn for full 64K" — matches §9 trap taxonomy (dense mask memory explosion)

## Key config dump

```json
{
  "architectures": ["ModernBertForSequenceClassification"],
  "hidden_size": 1024,
  "num_hidden_layers": 28,
  "global_attn_every_n_layers": 3,
  "max_position_embeddings": 65536,
  "rope_parameters": {
    "full_attention": {"rope_theta": 2560000.0},
    "sliding_attention": {"rope_theta": 10000.0}
  },
  "classifier_pooling": "cls",
  "id2label": {"0": "approve", "1": "deny"}
}
```
Other verified configs:
- `LFM2.5-230M`: `lfm2` hybrid, `hidden 1024`, `14 layers`, `max_position 128000` (card says 32K, config advertises 128K — trust 32K), `vocab 65536`, `rope_theta 1M`, `Lfm2ForCausalLM`
- `Qwen3-0.6B`: `qwen3`, `hidden 1024`, `28 layers`, `max_position 40960`, `vocab 151936`, `Qwen3ForCausalLM`
- `Gemma-3-270M`: gated, 5:1 local/global SW 1024, `RoPE 1M global/10K local`, 256K vocab, 32K verified (via web, not HF raw due 401)

## Implications

- **auto-0.4b is sweet-spot T3**: 395M encoder beats 600M decoder on paper for §3 (no generation waste, bidirectional, tiny head). Only 2-label head — rule ID would need separate head (§18 multi-head C) or decoder for `BLOCK:R17`.
- Long-context robustness is *trained*, not just RoPE: two-stage (bulk short + up-to-64K stage with random-depth burying)
- CPU runtime: PT + `flash_attention_2` is required path; ONNX not viable >8K — do not reject encoder, fix runtime (§9 trap)
- Next: run `scripts/run_baseline_matrix.py` dry-run now, real weighted run when GPU available; also reproduce threshold via reliability curves §30

Artifacts: `reports/config_ProCreations__auto-0.4b.json`, `reports/config_Qwen__Qwen3-0.6B.json`, etc.
