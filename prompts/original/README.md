# SYNTHETIC PLACEHOLDER — superseded, do not train on this

`policy.txt` here is **not** the real classifier prompt. It is a synthetic
placeholder, one 611-character rule repeated **177 times** (108,313 chars total,
SHA-256 `e091da1dca04950c8da4089aab8fb180adf2154aa8b2fc3764f3a5b98ef7e44d`).

## The real prompt

It now lives at `prompts/claude-code-2.1.289/`, in two variants captured from the
real bundle (Claude Code / cfork 2.1.289):

- `stage1_severity.txt` — severity-enabled branch
- `stage2_verdict.txt` — severity-disabled branch (default)

Verify with:

```bash
sha256sum -c prompts/manifests/claude-code-2.1.289.sha256
```

## Why the placeholder is worse than useless for training

It contains exactly **one** distinct rule. A retrieval test needs distinct targets,
so with one rule copied 177 times a model can score well by pattern-matching that
string anywhere in the input, and removing the policy removes no information the
action and transcript did not already carry.

This is precisely why the original Stage 0 policy ablation (`none ≈ full`, 0.873 vs
0.883) could not conclude anything about in-context application. That report is now
retracted; see `reports/stage0_findings.md` §0.

## Do not delete without care

Code in `src/prompt/builder.py` and `src/training/` reads
`prompts/original/policy.txt` and the manifest at
`prompts/manifests/policy.sha256`, and the benchmark harness aborts on a hash
mismatch. When the pipeline is repointed at the real prompt, update the builder's
path and regenerate the manifest together, or the abort will fire.