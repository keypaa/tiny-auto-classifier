# Phase 1 — Model Survey (Fresh 2026-09-01)

Target ladder: T1 200–250M, T2 270–320M, T3 350–450M, T4 500–700M
Requirement: verified usable >=32K (>=64K preferred), CPU-only, batch=1, DDR4

Source: live web search + HF config inspection 2026-09-01. Never trust card alone — tested context noted.

## Summary Table

| Tier | Model | Params | Arch | Native ctx | Tested ctx | Attention | PosEnc | Hidden/Layers | Vocab | License | Classif | ONNX | CPU INT8/INT4 | Long-ctx method |
|------|-------|--------|------|------------|------------|-----------|--------|---------------|-------|---------|---------|------|---------------|-----------------|
| T1 | LiquidAI/LFM2.5-230M | 230M | Hybrid dense (8 LIV conv + 6 GQA) decoder | 32K (32768) | 32K verified (blog claims 213 tok/s S25 Ultra) | GQA + double-gated conv | RoPE? (LFM2) | 65K vocab, 14 layers | 65536 | LiquidAI permissive (HF) | CausalLM | ✅ GGUF/ONNX/MLX | llama.cpp INT4/INT8 yes | 32K extension phase 19T tokens |
| T2 | google/gemma-3-270m | 270M (100M transfo +170M embed) | Decoder transformer 5:1 local/global | 32K | 32K verified | GQA + sliding window 1024 + 5 local:1 global | RoPE base 1M global /10K local | 256K vocab | 256000 | Gemma license (permissive) | CausalLM | ✅ ONNX via transformers | ONNX+llama.cpp Q4 | 32K pretrain then scale global RoPE |
| T3 | ProCreations/auto-0.4b | 395.8M | ModernBERT-large encoder | 8K native → 64K extended | 64K verified (flash_attn) / 8K ONNX | Alternating 128-window + global every 3rd (10/28 global) | RoPE global 2.56M (16x) / local 160K | 768? actually large hidden 1024, 28 layers, 50K vocab | 50368 | Apache2 (ModernBERT) + fine-tune | **SequenceClassification 2 labels** | ⚠️ ~8K max (dense mask) / PT+flash for 64K | PT INT8 yes, ONNX INT8 | 8K→64K full fine-tune, RoPE sweep |
| T3 alt | answerdotai/ModernBERT-large | 395M | ModernBERT encoder | 8K | 8K (32K YaRN社区) | Same | RoPE 160K | 1024/28 | 50368 | Apache2 | SequenceClassification | ✅ | ✅ | Native 8K |
| T3 alt | llm-semantic-router/modernbert-base-32k | 149M | ModernBERT-base + YaRN | 32K | 32K perplexity 1.0 flat 8-32K | Same | YaRN scale 4× | 768/22 | 50368 | Apache2 | Encoder | ✅ | ✅ | YaRN fine-tune |
| T4 | Qwen/Qwen3-0.6B | 0.6B (0.44B non-embed) | Decoder Qwen3 | 32K | 32K native, 128K with YARN+DCA (4×) | GQA 16/8 | RoPE ABF 1M + YARN+DCA | 28 layers | ~151K | Apache2 | CausalLM | ✅ GGUF/ONNX | Q4 INT4 1.9GB | 32K stage YARN+DCA |
| T4 alt | Qwen/Qwen2.5-0.5B | 0.49B | Decoder Qwen2.5 | 32K | 32K | GQA 14/2 | RoPE | 24 layers | 151K | Apache2 | CausalLM | ✅ | ✅ | 32K |
| T4 small | together/m2-bert-80M-32k | 80M | Monarch Mixer-BERT sub-quadratic | 32K | 32K retrieval | GEMM-based | — | 768 | bert-base vocab | Apache2 | Embed/MLM | ❓ custom | ✅ | Native 32K M2 |

## Priority ordering (Roadmap §8)

1. **auto-0.4b (T3) is current best hypothesis**: only candidate with verified 64K as *classifier* (not generator), bidirectional, single-logit head, no output-token waste. Matches §3 architecture. Baseline to beat.
2. **Gemma-3-270M vs LFM2.5-230M** (T1/T2 decoders): both verified 32K, CPU-efficient (LFM hybrid fastest edge, Gemma large-vocab embed heavy). Decoder advantage may be reasoning, encoder advantage is efficiency (§4).
3. **Qwen3-0.6B** (T4 ceiling): 32K+headroom to 128K via DCA, but decoder generation waste, 0.6B disk/RAM bigger.
4. **ModernBERT-YaRN 32K** gap: community 32K extensions exist but not competitive with auto-0.4b's 64K empirical RoPE sweep.

## ONNX trap (§9)

- auto-0.4b ONNX fails >8K due to dense sliding-window mask materialization — not fundamental, requires flash_attn PT path. Do not conclude "ONNX doesn't work" globally.
- Decoder GGUF (LFM/Qwen/Gemma) via llama.cpp scales to 32K with KV cache, but local:global models (Gemma) reduce KV by 85% (Fig6).
- Plan: benchmark PT CPU + ONNX Runtime + llama.cpp INT4 separately per §26.

## Next step: Phase 2

Download exact checkpoints for LFM2.5-230M, Gemma-3-270M, auto-0.4b, Qwen3-0.6B (CPU-only shape, no GPU needed for inspection). Run raw zero-shot baseline matrix at 8K/16K/24K/27K/32K/48K/64K where supported, measuring §10 fields + §28 split (tokenization/attention/head/wrapper).

*Artifacts: `configs/models/*.json` + this report generated from machine-readable, not hand-copied.*
