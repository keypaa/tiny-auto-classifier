# Transcript samples — real `L3t` / `O3t` output (Claude Code 2.1.289)

These are **not hand-written**. `transcript` and `action` were produced by
importing the real `chunk-wgfrtm7w.js` under Bun and calling its own exported
serializers:

- `eMo(messages, tools, opts)` → the `<transcript>` body
- `cHr(actionMessage, tools)` → the action text after `</transcript>`

## How to reproduce

```bash
python3 tools/extract-bundle.py <2.1.289 binary> --force --out /tmp/work-289
mkdir -p /tmp/clsrun && cd /tmp/clsrun
cp /tmp/work-289/runtime/assets/* .
sed -i 's|/$bunfs/root/|./|g' *.js        # remap Bun's virtual FS to real paths
cp harness.mjs . && bun harness.mjs
```

The chunks carry a stale `// @bun @bytecode` marker but are plain JS, so they
load directly once imports resolve. All 233 embedded assets must be copied or
module init fails.

## Wire format

```
<transcript> {"user":"…"}              ← one JSON object per line, trailing \n
{"Bash":"ls /"}
</transcript> {"Bash":"rm -rf /"}      ← action last, structurally separated
```

## Fidelity boundary — read before training on this

**Real (verbatim from the bundle):** the transcript/action envelope, key naming
(`FXo`), id truncation (`rfe` = last 6 chars), control-char escaping (`SL`),
**injection bracketing** (`NT`/`nI`), 3-space indentation (`ep`), block
splitting, the `D3t` JSON fallback, and the fail-open empty path.

**Approximated:** the built-in tool specs. `D3t` calls
`spec.toAutoClassifierInput(input)` to project each tool's input down to
classifier-relevant fields. The real specs live behind the tool registry, which
needs the whole tool layer instantiated to reach. `harness.mjs` supplies
projections in the same style (`Bash`→`command`, `Edit`→`file_path`, …).

So: **line shape is exact, per-tool field selection is not.** For the true
projection per tool, capture live calls or dump the registry.

## Behaviours confirmed empirically

| finding | evidence |
|---|---|
| `Read`, `Grep`, `Glob` **never reach the classifier** | hardcoded exclusion set `MXo`; probed 22 tool names |
| Forged `<transcript>` / `<forwarded_turn>` in user text get bracketed | `[transcript>…[/transcript>` in `injection-attempt` |
| `classifierMetaLines` bypasses all escaping | `L3t` meta branch is `return e.serialized`, raw |
| Unregistered tool → empty action → fail-open | `action: ''`; `U3t()` allows without calling the model |
| Outcome lines (`{"outcome":"ok"}`) are **off by default** | gate `tje()`→`fMt()` defaults false; setting `CLAUDE_CODE_AUTO_MODE_OUTCOME_CODES=1` did not flip it under Bun |
| `hostClassifierContext` renders even when outcomes are off | `{"host_context":"…","id":"0000AA"}` |

The bracketing is the important one: it is why a user cannot forge the
structural delimiters the classifier uses to separate evidence from the action.
Note the bracket placement (`[transcript>`, not `[<transcript>]`) is lossy — the
leading `<` is consumed. Treat it as a *disarm*, not a *sanitize to a known
format*; content after the marker is still attacker-controlled and the
classifier is told to read it as data.
