# CPU serving cache — design note

*Status: proposal. Nothing here is built. Numbers marked ~ are extrapolations
from the 8K CPU measurement, to be replaced by single-request profiling.*

## Why

Per-decision cost on CPU is dominated by prompt prefill, not by the decision
itself. Measured: ~102 s for an 8K-token forward on the Ryzen 4500U (fp32,
single forward). A 30–45K-token classifier prompt extrapolates to several
minutes per decision at full prefill. Everything after prefill — the
transcript, the action, the verdict decode — is hundreds of tokens. Without a
cache, the product is unusable. With one, each decision costs a short tail.

Anthropic's answer is verified (analysis/Q11): `cache_control` breakpoints,
5-minute TTL, ~30K shared prefix cached once, second stage reusing the first
stage's prefix, suffix-only uncached input per call. Ours is the same shape,
adapted to a local static model.

## Design

**Cache key: (prompt_sha256, weights id).** The prompt is byte-addressed by
its Yqr-resolved SHA (see `prompts/claude-code-2.1.289/README.md`). Any slot
change — user rules added, `$defaults` dropped, deny block rendered — produces
a new SHA and a cold prefill. The populated-config collapse (141K → 60K chars)
is therefore a second cache entry, not a corruption of the first. Weights id
covers base checkpoint plus adapter hash; any retrain invalidates everything.

**Store: KV cache for the shared prefix, RAM-resident.** Approximate size for
a 150M-class encoder (22 layers, hidden 768) at 30K tokens:
`2 × layers × seq × hidden × bytes_per_value` ≈ 4 GB in fp32, ~1 GB in int8.
Quantization halves the cache twice: faster prefill to fill it, smaller entry
to keep it. Exact figures at build time; the formula is what sizes the box
(21 GB here — fits, with room for one or two prompt variants, not dozens).

**Per call:** feed only transcript + action + suffix against the cached
prefix. Suffix-equivalent (our output contract) stays outside the cached
span, same as their suffix-after-breakpoint layout.

**Eviction: LRU with session affinity, RAM-only for v1.** No disk
persistence initially: a 1–4 GB write per new prompt version buys little when
re-prefill is a one-time cost per session, and cold restart re-prefill is
acceptable. Revisit if session churn proves otherwise.

**TTL:** align with observed server behavior only as a starting point; local
policy is LRU pressure, not wall-clock expiry. No remote invalidation source
exists offline.

## Must measure first

Single-request CPU latency split into prefill vs decode on the actual
candidate (200m-2 + int8), at 8K / 30K / 45K prefix lengths. Those three
numbers size the cache, set the truncation policy, and decide whether v1
ships. Until they exist, every figure above the formula is provisional.

## Non-goals for v1

Cross-session cache sharing, distributed or multi-box caching, adaptive
truncation driven by cache hit rate. Also explicitly out: caching across
different weights ids — silent reuse across a retrain would be a correctness
bug, and the key makes it structurally impossible.
