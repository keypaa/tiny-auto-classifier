# Q3 — Per-tool classifier input projections (`toAutoClassifierInput`)

**Version:** Claude Code 2.1.289 · **Source:** `/tmp/rev/` extraction, copied to
`samples/tool-specs/`

## The dispatcher

```js
// chunk-wgfrtm7w.js @2698797
function D3t(e, n, r, s) {
  let g = () => b(r);                                   // b = JSON.stringify (chunk-sdt6jsgm.js @46404)
  try { return e.toAutoClassifierInput(r) ?? g() }
  catch (h) { /* logged + telemetry, then */ return g() }
}
```

**`??` not `||`.** A method returning `""` does **not** trigger the JSON fallback — only
`undefined` or a throw does. `sfe()` treats `""` as *not classifier-relevant*, so the
entry is dropped entirely and can hit the `U3t()` fail-open path.

## Registry — instantiation path EXISTS

Exported from `chunk-b2sa2xdg.js`:

```js
var EG = { getAllBaseTools: CE, getTools: jM, assembleToolPool: wRt };
```

Threaded onto the context as `options.toolCatalog`, alongside `options.tools`:

| accessor | returns |
|---|---|
| `getAllBaseTools()` → `CE()` | flat base array, feature-gated entries included |
| `getTools(options, opts)` → `jM()` | **this produces `options.tools`** — same array after `isEnabled()`, permission (`y4()`), simple-mode and REPL filtering |
| `assembleToolPool(permCtx, mcpTools, opts)` → `wRt()` | `getTools()` ⨯ de-duplicated MCP + machine tools |

Earlier probes missed this because the catalog is an object of *functions*, not an
array — `Array.isArray()` scanning over exports can never find it.

## Priority tools

| tool | projection | notes |
|---|---|---|
| **Bash** | `command` (bare string) | becomes an object only when a remote host or domain list is present: `command` + `_host` (real machine id only — not `"container"`/`"this-machine"`, truncated to 40 chars, `[\p{Cc}\p{Cf}]` stripped) + `allowed_domains` (non-empty all-string array only) |
| **Write** | `` `${file_path}: ${content}` `` | local. Remote → `{file_path, content, _host, _host_context}` |
| **Edit** | **two modes** | see below |
| **NotebookEdit** | **two modes** | see below |
| **WebFetch** | `url`, or `` `${url}: ${prompt}` `` | |
| **WebSearch** | `query` | chunk-b2sa2xdg.js @47105 |
| **Task** | **alias of `Agent`** | `` `(${subagent_type}): ${prompt}` `` or `` `: ${prompt}` `` |
| **KillShell** | **alias of `TaskStop`** | `task_id ?? shell_id ?? ""` |
| **TodoWrite** | `` `${todos.length} items` `` | **only the count.** No todo text ever reaches the classifier. |
| **Skill** | `skill` | |
| **AskUserQuestion** | `questions[]` → `question`, `header`, `multiSelect`, `options[].label/.description/.preview`; gated variant adds `kind`, `description`, `placeholder`, `min`, `max`, `step`, `defaultValue`, `unit`, top-level `title` | sanitizer `Izr` collapses whitespace and escapes `\` and `"`. Caps: 256 rendered entries, question-count ceiling `4294967295` |
| **SendMessage** | `to`, `message`/`summary` | `message` may be a tagged object: `shutdown_request` / `shutdown_response` / `plan_approval_response` (pulls `approve`, `request_id`, `reason`/`feedback`) |
| **SendUserMessage** (alias `Brief`) | `message` | |
| **ScheduleWakeup** | branch selector on `stop` / `delaySeconds`+`prompt` | emits `stop the /loop — …`, `malformed … missing delaySeconds/prompt — …`, or `` `wake in ${delaySeconds}s: ${prompt}` `` |
| **ExitPlanMode** | **spec exists, method ABSENT** | falls through to raw `JSON.stringify(input)` |
| **MCP (generic)** | **every own key** | `key=value` pairs space-joined; objects/arrays `JSON.stringify`'d, rest `String()`'d; empty input → bare tool name. **No field filtering at all** — sharpest contrast with `TodoWrite` |

### Edit — two modes

Gate is `xhe()` → `CLAUDE_CODE_AUTO_MODE_EDIT_REMOVAL` (default **off**).

```js
toAutoClassifierInput(e) {
  let n = ede(e);
  if (!xhe()) return n === void 0 ? `${e.file_path}: ${e.new_string}`
                                  : {file_path, new_string, _host, _host_context};
  let r = jfn(), s = e.old_string, g = r > 0 && typeof s === "string" && s.length > r;
  return { file_path, adds: e.new_string, removes: g ? s.slice(0, r) : s,
           ...(r > 0 && {removesTruncated: g}),
           ...(e.replace_all === true && {replaceAll: true}), _host, _host_context };
}
```

Default: `file_path`, `new_string`. **`old_string` is dropped entirely** — the default
projection cannot see what an edit removes.

Removal-aware: adds/removes, cap `jfn()` = `CLAUDE_CODE_AUTO_MODE_EDIT_REMOVAL_CAP`
(default `3000`), `removesTruncated`, `replaceAll`.

### NotebookEdit — two modes, same gate

Default: `` `${notebook_path} ${edit_mode ?? "replace"}: ${new_source}` `` —
`cell_id`/`cell_type` dropped.

Removal-aware: `notebook_path`, `mode`, optional `cell_id`, `cell_type` (omitted when
`mode === "delete"`), then `ignored_source` (= `new_source`) for delete, else `adds`.

## Do not exist in 2.1.289

Only legacy string literals in permission lists; **no spec, no projection**:

`MultiEdit` · `SlashCommand` (replaced by `Skill`) · `BashOutput` · `LS`

`Task` and `KillShell` exist only as **aliases**. `SendUserMessage` aliases `Brief`.

## Inventory

- 97 `toAutoClassifierInput` occurrences across 38 chunks
- 82 tool specs built by the `Gt({name:…})` factory, plus `Artifact` as a plain literal
- **53 specs carry the method**; **24 do not** and fall back to `JSON.stringify(input)`:
  `ExitPlanMode`, `LSP`, `TaskList`, `CronList`, `EnterPlanMode`, `SendFeedback`, `Poll`,
  `TestingPermission`, `SuggestSkills`, `ToolSearch`, `WaitForMcpServers`, and all seven
  `self_hosted_runner_*` readers

## Training-relevant consequences

1. **`TodoWrite` leaks nothing.** A model cannot be trained to judge todo *content* —
   the content never reaches it. Only `"N items"`.
2. **Default `Edit` hides removals.** Without `CLAUDE_CODE_AUTO_MODE_EDIT_REMOVAL=1`,
   a delete-via-edit looks identical to an add. If the training set assumes it can see
   `old_string`, it is wrong about the default deployment.
3. **MCP tools pass everything through.** Any field an MCP server declares reaches the
   classifier verbatim as `key=value`. This is the widest injection surface in the
   system and the one most likely to be attacker-influenced.
4. **24 tools send raw JSON.** Your training data must include full-JSON-shaped actions
   or the model will not expect them.

## Confidence

**VERIFIED** (read from source): every method body above; all tool-name resolutions via
a cross-chunk import/export resolver (2164 chunks, 29,445 exports), cross-checked against
literal strings in the defining chunks; registry wiring; `_host`/`_host_context`
constants; `b = JSON.stringify`; the `??` semantics; the absence of the four removed
specs and of the method on `ExitPlanMode`.

**INFERRED** — flagged in the source report, treat as lower confidence:

1. `xhe()` read as an "edit-removal visibility" gate purely from its env var name; the
   bundle never names the branch.
2. `wh()` / `Ype()` not resolved to root — inferred to be remote/host-capability flags
   from usage (`fV(){return a.CLAUDE_CODE_REMOTE ? "container" : "this-machine"}`).
   Only gates `_host` emission.
3. `Zr(e)` inside `Artifact`'s dispatcher unresolved; per-action sub-handler dispatch
   inferred from 13 sibling methods keyed on `e?.action === …`.
4. `send_message` mapped to `SendMessage` + `SendUserMessage` on field-shape grounds.
5. `Gt(` assumed to be the dominant factory. Exactly one non-`Gt` tool
   (`Artifact`) was found among those carrying the method, but a structurally different
   factory could hide others.

## Reproduce

```bash
cd samples/tool-specs/scripts
python3 xref.py          # build cross-chunk import/export graph
python3 resolve_names.py # recover minified identifier → real tool name
python3 named.py         # map spec → name, dump method bodies
python3 specs2.py        # classify specs by method presence
```
