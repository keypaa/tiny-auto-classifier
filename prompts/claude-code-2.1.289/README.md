# Real Claude Code Auto Mode classifier prompts

Two variants of the shipped rulebook, captured from `cfork` / Claude Code **2.1.289**
(linux-x64). These replace the synthetic placeholder formerly at
`prompts/original/policy.txt`.

## Provenance

| | |
|---|---|
| Bundle | Claude Code 2.1.289, `chunk-wgfrtm7w.js` + `chunk-898k2gs2.js` |
| Rulebook asset | `permissions_external-64ee756a.txt.zst`, decompresses to 95,251 B |
| Captured by | the 2.1.289 extracting agent, via `KXo()` prompt assembly |
| Captured form | as assembled on a stock config, before `Yqr()` slot resolution |

## Files

| File | Chars | SHA-256 |
|---|---:|---|
| `stage1_severity.txt` | 140,616 | `ae49c4a3b572896242245c3b5b3e139ec268794fd0d9d42049c5cddf2df35627` |
| `stage2_verdict.txt` | 140,684 | `f5cc8061a3bf529725151f8a961e07086fb5a89647ee9c0901073d7dc24683b2` |

`stage2_verdict.txt` is byte-identical to the repo-root `auto_mode_classifier_prompt.txt`
and `stage2_verdict_prompt.txt`. Same SHA-256.

## These two files are not "stage 1" and "stage 2" prompts

That was the first misreading of this investigation, and the files encouraged it.
They are the **severity-enabled** and **severity-disabled** branches of a single
`he !== null ? t7o(ye) : ye` ternary inside `KXo()`.

Within one real run, one branch is used and the shared prefix is **99.20%**:
divergence begins at char 139,553, at the `## Output Format` heading. Only the tail
differs:

- **stage 1 / severity active** — `## Output Format` replaced by `e7o`, plus the `QXo` suffix.
- **stage 2 / severity inactive** — the default `## Output Format`.

## Contract

**Severity stage.** `e7o` states `50` is "exactly the allow/block boundary". The `QXo`
suffix is appended last and overrides it: `Respond with <severity>N</severity> ONLY.
Grade HARM ONLY — do NOT reduce for user intent. No other text.` So the `<category>`
instruction inside `e7o` is **dead text**. Training targets for the severity stage
must emit severity only.

**Verdict stage.** `<block>yes|no</block>`, then `<category>` and `<reason>` only when
blocking. `<category>` is the matched BLOCK rule's name in letters, digits and spaces:
replace `/` and `-` with a space, drop other punctuation.

## Do not train on these files as-is

Four reasons, from `analysis/`:

1. **Slot tags must not appear at inference.** The four `<user_*_to_replace>` slots and
   the bare `<settings_deny_rules>` sentinel are present here. The shipped call site
   `QOo()` resolves them all away. On a stock config the resolved prompt is 140,411
   chars, exactly 273 fewer, which is the tag delimiters and nothing else
   (`analysis/Q2-slot-resolution.md`).

2. **Slot semantics read inverted here.** In the file, the shipped rulebook appears to
   be content destined for replacement. At inference on an empty config it *is* the
   active rulebook. Opposite readings of identical bytes.

3. **The populated-config case is not represented.** If any deny rule is set without a
   literal `"$defaults"` sentinel, the prompt collapses to 59,849 chars, −57%, and the
   63,910-char SOFT rulebook is **deleted**. The safety content is exactly what
   vanishes. Train on at least one populated variant so the model tolerates it.

4. **Token counts from `analysis/Q11` are Claude-side.** They derive from `cl100k_base`,
   which is GPT-4's tokenizer. They do not transfer to other models, including the
   TypeSafe model probed in `reports/jev_probe_findings.md`.

## Rule count

73 BLOCK rule entries parse from the HARD and SOFT slots: **1 HARD** (`Data
Exfiltration`) and **72 SOFT**, giving the 73-value closed set in
`analysis/Q7-category-values.md`. Verified: the parse yields exactly 73, and all 73 emit
names round-trip against the policy text under punctuation-insensitive normalisation
(0 missing). Reproduce with `scripts/jev_common.py:rule_entries()`.