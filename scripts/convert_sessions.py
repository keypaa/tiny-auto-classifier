#!/usr/bin/env python3
"""Convert raw Claude Code session JSONL into classifier (transcript, action) pairs.

Reads session files. NEVER writes to the source tree — output goes to --out
only. The export under data/import/ is left byte-identical.

Fidelity rules (all verified against the 2.1.289 bundle analysis):
  - MXo members (Read, Grep, Glob, LSP, ToolSearch, 3 MCP resource readers)
    never reach the classifier: dropped from evidence AND never emitted as an
    action (the driver bypasses them entirely — observed in volume).
  - No-spec tools (MultiEdit, SlashCommand, *Output*, LS, Frame*, Team*,
    SuggestBackgroundPR, AutofixPr): same treatment (Q6 class b).
  - Method-absent specs: D3t throws -> raw JSON.stringify(input) (Q6 class c).
  - EndConversation always projects "": as action -> fail-open row.
  - TaskStop/EnterWorktree: "" only when their required field is absent.
  - Edit/NotebookEdit: default gate (OLD string dropped, removals invisible).
  - Tool results, thinking blocks, isMeta and isSidechain records: excluded.
  - NT() bracketing applied to user/assistant text for the three reserved
    words; json.dumps provides SL() control-char escaping; multi-line content
    stays escaped inline (never literal) per Q13.
  - Secret shapes are COUNTED per pair (secret_hits) for the later redaction
    pass. Values are never printed. Nothing is redacted here.

Usage:
    python3 scripts/convert_sessions.py --src data/import --out data/imported_pairs.jsonl
    python3 scripts/convert_sessions.py --src ~/.claude/projects --out /tmp/probe.jsonl --max-files 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

# --- reachability classes (analysis/Q6, Q12) --------------------------------
MXO_DROP = {"Read", "Grep", "Glob", "LSP", "ToolSearch",
            "ListMcpResourcesTool", "ReadMcpResourceTool", "ReadMcpResourceDirTool",
            "ListMcpResources", "ReadMcpResource", "ReadMcpResourceDir"}
NO_SPEC = {"MultiEdit", "SlashCommand", "BashOutput", "BashOutputTool",
           "AgentOutput", "AgentOutputTool", "TaskOutput", "LS", "Frame",
           "FrameRead", "TeamCreate", "TeamDelete", "SuggestBackgroundPR",
           "AutofixPr"}
ALWAYS_EMPTY = {"EndConversation"}

# NT()/nI() reserved words: observed transform "<w>" -> "[w>", "</w>" -> "[/w>".
RESERVED_WORDS = ("transcript", "forwarded_user_turns", "forwarded_turn")

SECRET_PATTERNS = {
    "aws_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    "anthropic_key": re.compile(r"sk-ant-[A-Za-z0-9\-_]{10,}"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY"),
    "slack_token": re.compile(r"xox[bap]-"),
    "password_assign": re.compile(r"(?i)(password|passwd|secret|api[_-]?key)\s*[:=]\s*\S+"),
}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def nt_bracket(text: str) -> str:
    for w in RESERVED_WORDS:
        text = text.replace(f"<{w}>", f"[{w}>").replace(f"</{w}>", f"[/{w}>")
    return text


def secret_scan(text: str) -> dict:
    hits = sorted({name for name, rx in SECRET_PATTERNS.items() if rx.search(text)})
    emails = len(EMAIL_RE.findall(text))
    return {"secret_hits": hits, "email_count": emails}


def line(key: str, value) -> str:
    # Compact separators: JSON.stringify emits no spaces, and every shipped
    # sample is spaceless. Python's default (", ", ": ") would put a space
    # after every colon — a systematic byte-level deviation across all rows.
    return json.dumps({key: value}, ensure_ascii=False, separators=(",", ":")) + "\n"


# --- per-tool projections (samples/tool-specs/FINDINGS.md + spec snippets) --
def project(tool: str, inp: dict) -> tuple:
    """Return (value, approximated: bool, route: str).

    route in {typed, untyped-json, empty}. value None means drop (unreachable).
    """
    if not isinstance(inp, dict):
        inp = {}
    get = inp.get

    if tool in ("Bash", "PowerShell"):
        v = get("command")
        if not isinstance(v, str):
            return dict(inp), True, "untyped-json"
        return v, False, "typed"
    if tool == "Write":
        if not isinstance(get("file_path"), str):
            return dict(inp), True, "untyped-json"
        return f"{get('file_path')}: {get('content')}", False, "typed"
    if tool == "Edit":
        if not isinstance(get("file_path"), str):
            return dict(inp), True, "untyped-json"
        return f"{get('file_path')}: {get('new_string')}", False, "typed"
    if tool == "NotebookEdit":
        if not isinstance(get("notebook_path"), str):
            return dict(inp), True, "untyped-json"
        return (f"{get('notebook_path')} {get('edit_mode') or 'replace'}: "
                f"{get('new_string')}"), False, "typed"
    if tool == "WebFetch":
        url = get("url")
        if not isinstance(url, str):
            return dict(inp), True, "untyped-json"
        p = get("prompt")
        return (f"{url}: {p}" if isinstance(p, str) else url), False, "typed"
    if tool == "WebSearch":
        q = get("query")
        return (q, False, "typed") if isinstance(q, str) else (dict(inp), True, "untyped-json")
    if tool in ("Task", "Agent"):
        p = get("prompt")
        if not isinstance(p, str):
            return dict(inp), True, "untyped-json"
        st = get("subagent_type")
        return (f"({st}): {p}" if isinstance(st, str) else f": {p}"), False, "typed"
    if tool in ("KillShell", "TaskStop"):
        v = get("task_id", get("shell_id"))
        if v is None:
            return "", False, "empty"
        return v, False, "typed"
    if tool == "TodoWrite":
        todos = get("todos")
        if not isinstance(todos, list):
            return dict(inp), False, "untyped-json"  # throws -> raw JSON (Q12)
        return f"{len(todos)} items", False, "typed"
    if tool == "Skill":
        # Runtime-proven (corpus-10k via real serializers): {"Skill":"deploy"},
        # d-typed. The bundle scan missed the method; runtime wins.
        s = get("skill")
        if not isinstance(s, str):
            return dict(inp), True, "untyped-json"
        return s, False, "typed"
    if tool == "TaskCreate":
        s = get("subject")
        return (s, False, "typed") if isinstance(s, str) else (dict(inp), False, "untyped-json")
    if tool == "TaskUpdate":
        inner = get("input") if isinstance(get("input"), dict) else inp
        parts = [inner.get("taskId")]
        if inner.get("status"):
            parts.append(inner["status"])
        if inner.get("subject"):
            parts.append(inner["subject"])
        parts = [p for p in parts if isinstance(p, str)]
        return (" ".join(parts), False, "typed") if parts else (dict(inp), True, "untyped-json")
    if tool in ("GetTask", "TaskGet"):
        v = get("taskId")
        return (v, False, "typed") if v is not None else (dict(inp), False, "untyped-json")
    if tool == "CronDelete":
        v = get("id")
        return (v, False, "typed") if v is not None else (dict(inp), False, "untyped-json")
    if tool in ("PushNotification", "SendUserMessage"):
        v = get("message")
        return (v, False, "typed") if isinstance(v, str) else (dict(inp), True, "untyped-json")
    if tool == "ExitWorktree":
        v = get("action")
        return (v, False, "typed") if v is not None else (dict(inp), False, "untyped-json")
    if tool == "EnterWorktree":
        v = get("path", get("name"))
        return ("", False, "empty") if v is None else (v, False, "typed")
    if tool == "CronCreate":
        if not isinstance(get("cron"), str) or not isinstance(get("prompt"), str):
            return dict(inp), True, "untyped-json"
        return f"{get('cron')}: {get('prompt')}", False, "typed"
    if tool == "AskUserQuestion":
        # Runtime-proven shape: "\"Q?\" [header: H] options: (1) \"L\" — D (2) ...".
        # Izr sanitizer collapses whitespace and escapes \ and ".
        def _izr(s):
            return re.sub(r"\s+", " ", str(s)).replace("\\", "\\\\").replace('"', '\\"')
        qs = get("questions")
        if not isinstance(qs, list):
            return "[malformed AskUserQuestion input]", False, "typed"
        parts = []
        for q in qs[:256]:
            if not isinstance(q, dict):
                continue
            seg = f"\"{_izr(q.get('question', ''))}\""
            if q.get("header"):
                seg += f" [header: {_izr(q['header'])}]"
            opts = q.get("options") if isinstance(q.get("options"), list) else []
            if opts:
                rendered = []
                for i, o in enumerate(opts, 1):
                    if not isinstance(o, dict):
                        continue
                    item = f"({i}) \"{_izr(o.get('label', ''))}\""
                    if o.get("description"):
                        item += f" — {_izr(o['description'])}"
                    rendered.append(item)
                seg += " options: " + " ".join(rendered) if rendered else ""
            parts.append(seg)
        return " ".join(parts), False, "typed"
    if tool == "SendMessage":
        to, msg = get("to"), get("message", get("summary"))
        if isinstance(msg, str):
            return (f"{to}: {msg}" if isinstance(to, str) else msg), True, "typed"
        return dict(inp), True, "untyped-json"
    if tool == "SubagentHandback":
        m = get("message")
        if not isinstance(m, str) or not m.strip():
            return dict(inp), True, "untyped-json"
        a = get("agent")
        return (f"{a}: {m}" if isinstance(a, str) else m), True, "typed"
    if tool == "ListAgents":
        return "list agents", False, "typed"
    if tool == "ReportFindings":
        f = get("findings")
        if not isinstance(f, list):
            return dict(inp), True, "untyped-json"
        return f"{len(f)} findings", False, "typed"
    if tool in ("ScheduleWakeup", "SendFile", "SendUserFile", "FetchInboxMessage",
                "ProposeGoal", "ShareOnboardingGuide", "ReadNotifications",
                "ShowOnboardingRolePicker", "DesignSync", "propose_skills",
                "memory_list", "memory_read", "memory_write", "AppifactRepl",
                "Workflow", "ClaudeDesign", "Projects", "RemoteTrigger",
                "self_hosted_runner_requeue_session", "self_hosted_runner_spawn_local"):
        return dict(inp), True, "untyped-json"  # complex/rare: raw JSON, flagged
    # Default: method presence unknown -> raw JSON fallback, flagged.
    # MCP tools (mcp__*) land here: bundle has no per-tool method, D3t throws.
    return dict(inp), True, "untyped-json"


def user_text_parts(content) -> list[str]:
    """Extract display text from a user message; skip tool results/images."""
    if isinstance(content, str):
        return [content] if content.strip() else []
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                t = b.get("text", "")
                if isinstance(t, str) and t.strip():
                    out.append(t)
        return out
    return []


def convert_file(path: Path, stats: Counter) -> list[dict]:
    pairs: list[dict] = []
    evidence: list[str] = []
    n_pre = 0
    prior_assistant = False
    session_id = ""
    version = ""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError as exc:
        stats["files_unreadable"] += 1
        print(f"skip {path}: {exc}", file=sys.stderr)
        return pairs
    for lineno, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            r = json.loads(raw)
        except json.JSONDecodeError:
            stats["lines_unparseable"] += 1
            continue
        rtype = r.get("type")
        stats[f"rec:{rtype}"] += 1
        if r.get("isMeta"):
            stats["skipped:meta"] += 1
            continue
        if r.get("isSidechain"):
            stats["skipped:sidechain"] += 1
            continue
        session_id = session_id or r.get("sessionId", "")
        version = version or str(r.get("version", ""))
        if rtype == "user":
            for t in user_text_parts((r.get("message") or {}).get("content")):
                evidence.append(line("user", nt_bracket(t)))
        elif rtype == "assistant":
            content = (r.get("message") or {}).get("content") or []
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")
                if btype == "thinking":
                    stats["skipped:thinking"] += 1
                elif btype == "text":
                    t = b.get("text", "")
                    if isinstance(t, str) and t.strip():
                        evidence.append(line("assistant", nt_bracket(t)))
                        prior_assistant = True
                elif btype == "tool_use":
                    stats["tool_use:seen"] += 1
                    name = b.get("name", "")
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    if name in MXO_DROP or name in NO_SPEC:
                        stats["tool_use:mxo-or-nospec-dropped"] += 1
                        continue
                    proj, approx, route = project(name, inp)
                    if route == "empty" or (isinstance(proj, str) and proj == "" and name in ALWAYS_EMPTY):
                        pairs.append(mkrow(path, session_id, version, evidence, "",
                                           name, n_pre, prior_assistant, "fail-open",
                                           False, stats))
                        stats["pairs:fail-open"] += 1
                        continue
                    if proj is None:
                        stats["tool_use:dropped"] += 1
                        continue
                    action = line(name, proj)
                    pairs.append(mkrow(path, session_id, version, evidence, action,
                                       name, n_pre, prior_assistant,
                                       f"c-{route}" if route == "untyped-json" else
                                       "d-object" if isinstance(proj, (dict, list)) else
                                       "d-typed",
                                       approx, stats))
                    stats["pairs:emitted"] += 1
                    evidence.append(action)
                    n_pre += 1
                else:
                    stats[f"skipped:block-{btype}"] += 1
    return pairs


def mkrow(path, session_id, version, evidence, action, tool, n_pre,
          prior_assistant, reach_class, approx, stats, extra=None, scan=None):
    transcript = "".join(evidence)
    if scan is None:
        scan = secret_scan(transcript + action)
    if scan["secret_hits"]:
        stats["pairs:with-secret-hits"] += 1
    row = {
        "source": "imported",
        "session_file": path.name if hasattr(path, "name") else str(path),
        "session_id": session_id,
        "session_version": version,
        "tool": tool,
        "transcript": transcript,
        "transcript_empty": not transcript,
        "action": action,
        "action_projected_empty": not action,
        "pre_actions": n_pre,
        "prior_assistant_present": prior_assistant,
        "reachability_class": reach_class,
        "projection_approximated": approx,
        "secret_hits": scan["secret_hits"],
        "email_count": scan["email_count"],
    }
    if extra:
        row.update(extra)
    return row


def evidence_append_tool_use(name, inp, evidence, ctx, pairs, stats,
                             scan_state=None):
    """Shared emission for one tool_use. Returns updated n_pre increment (0/1).

    ctx: dict with path, session_id, version, n_pre, prior_assistant, extra.
    Unreachable tools are skipped entirely (never evidence, never a pair).
    scan_state: optional dict with scanned_len/emails/hits for incremental
    secret scanning on long sessions (avoids rescanning the whole prefix).
    """
    if name in MXO_DROP or name in NO_SPEC:
        stats["tool_use:mxo-or-nospec-dropped"] += 1
        return 0
    proj, approx, route = project(name, inp)
    if route == "empty" or (name in ALWAYS_EMPTY and proj == ""):
        scan = incremental_scan(evidence, "", scan_state) if scan_state else None
        pairs.append(mkrow(ctx["path"], ctx["session_id"], ctx["version"],
                           evidence, "", name, ctx["n_pre"],
                           ctx["prior_assistant"], "fail-open", False,
                           stats, ctx.get("extra"), scan=scan))
        stats["pairs:fail-open"] += 1
        return 0
    if proj is None:
        stats["tool_use:dropped"] += 1
        return 0
    action = line(name, proj)
    scan = incremental_scan(evidence, action, scan_state) if scan_state else None
    pairs.append(mkrow(ctx["path"], ctx["session_id"], ctx["version"],
                       evidence, action, name, ctx["n_pre"],
                       ctx["prior_assistant"],
                       "c-untyped-json" if route == "untyped-json" else
                       "d-object" if isinstance(proj, (dict, list)) else
                       "d-typed",
                       approx, stats, ctx.get("extra"), scan=scan))
    stats["pairs:emitted"] += 1
    evidence.append(action)
    return 1


def incremental_scan(evidence, action, state):
    """Scan only text not yet scanned; accumulate hits across the session."""
    # evidence is a list of lines; find first unscanned line by char offset
    todo = []
    pos = 0
    for e in evidence:
        if pos + len(e) > state["scanned_len"]:
            start = max(0, state["scanned_len"] - pos)
            todo.append(e[start:])
        pos += len(e)
    todo.append(action)
    blob = "".join(todo)
    for name, rx in SECRET_PATTERNS.items():
        if rx.search(blob):
            state["hits"].add(name)
    state["emails"] += len(EMAIL_RE.findall(blob))
    state["scanned_len"] = pos + len(action)
    return {"secret_hits": sorted(state["hits"]), "email_count": state["emails"]}


NOISE_MARKERS = ("<local-command-caveat>", "<command-name>", "<command-message>",
                 "<command-args>", "<local-command-stdout>")
SKILL_DUMP_MARK = "Base directory for this skill"


def keep_user_text(t: str) -> bool:
    if not isinstance(t, str) or not t.strip():
        return False
    if t.startswith("## Context Usage"):
        return False
    return not any(m in t for m in NOISE_MARKERS)


def convert_export_file(path: Path, stats: Counter) -> list[dict]:
    """Reader for cumulative conversation exports (messages-array schema)."""
    pairs: list[dict] = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError as exc:
        stats["files_unreadable"] += 1
        print(f"skip {path}: {exc}", file=sys.stderr)
        return pairs
    sessions: dict[str, list] = {}
    for raw in lines:
        if not raw.strip():
            continue
        try:
            r = json.loads(raw)
        except json.JSONDecodeError:
            stats["lines_unparseable"] += 1
            continue
        if not isinstance(r.get("messages"), list):
            stats["lines:not-export-group"] += 1
            continue
        sessions.setdefault(r.get("session_id", "?"), []).append(r)
    stats["export:sessions"] += len(sessions)
    for sid, groups in sessions.items():
        seen_hashes: set[str] = set()
        ordered = []
        for g in groups:
            msgs = g.get("messages", [])
            h = hashlib.sha1(json.dumps(msgs, sort_keys=True,
                                       ensure_ascii=False).encode()).hexdigest()[:16]
            if h in seen_hashes:
                stats["export:duplicate-groups"] += 1
                continue
            seen_hashes.add(h)
            ts = min((m.get("timestamp", "") or "" for m in msgs
                      if isinstance(m, dict)), default="")
            ordered.append((ts, g, msgs))
        ordered.sort(key=lambda t: t[0])
        evidence: list[str] = []
        n_pre = 0
        prior_assistant = False
        scan_state = {"scanned_len": 0, "hits": set(), "emails": 0}
        project = groups[0].get("project_path", "") if groups else ""
        for _, g, msgs in ordered:
            for m in msgs:
                if not isinstance(m, dict):
                    continue
                role = m.get("role")
                if role == "user":
                    content = m.get("content")
                    texts = ([content] if isinstance(content, str)
                             else [b.get("text", "") for b in content
                                   if isinstance(b, dict) and b.get("type") == "text"])
                    for t in texts:
                        if keep_user_text(t):
                            evidence.append(line("user", nt_bracket(t)))
                            if SKILL_DUMP_MARK in t or len(t) > 5000:
                                stats["export:skill-dumps"] += 1
                        else:
                            stats["export:user-noise-skipped"] += 1
                elif role == "assistant":
                    if m.get("model") == "<synthetic>":
                        stats["export:synthetic-skipped"] += 1
                        continue
                    for tu in (m.get("tool_uses") or []):
                        if not isinstance(tu, dict):
                            continue
                        stats["tool_use:seen"] += 1
                        ctx = {"path": path, "session_id": sid, "version": "",
                               "n_pre": n_pre, "prior_assistant": prior_assistant,
                               "extra": {"acting_model": m.get("model", ""),
                                         "caller": (tu.get("caller") or {}).get("type", ""),
                                         "project_path": project,
                                         "export_file": path.name}}
                        n_pre += evidence_append_tool_use(
                            tu.get("name", ""), tu.get("input"), evidence,
                            ctx, pairs, stats, scan_state)
                    text = m.get("content")
                    if isinstance(text, str) and text.strip():
                        evidence.append(line("assistant", nt_bracket(text)))
                        prior_assistant = True
                else:
                    stats[f"export:role-{role}"] += 1
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, required=True,
                    help="session .jsonl file or directory (read-only)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-files", type=int, default=0)
    args = ap.parse_args()

    if args.out.exists():
        sys.exit(f"refusing to overwrite {args.out}")
    if not args.src.exists():
        sys.exit(f"missing {args.src}")

    files = ([args.src] if args.src.is_file()
             else sorted(args.src.rglob("*.jsonl")))
    if args.max_files:
        files = files[: args.max_files]
    print(f"files: {len(files)} (source untouched; reading only)")

    stats: Counter = Counter()
    out_fh = open(args.out, "w", encoding="utf-8")
    n_total = 0
    seen_pairs: set[str] = set()  # global: exports are cumulative, sessions repeat
    for i, f in enumerate(files, 1):
        first = ""
        try:
            with open(f, errors="replace") as fh:
                for raw in fh:
                    if raw.strip():
                        first = raw
                        break
        except OSError:
            pass
        try:
            probe = json.loads(first) if first else {}
        except json.JSONDecodeError:
            probe = {}
        if isinstance(probe.get("messages"), list):
            stats["files:export-format"] += 1
            batch = convert_export_file(f, stats)
        else:
            stats["files:session-format"] += 1
            batch = convert_file(f, stats)
        wrote = 0
        for p in batch:
            k = hashlib.sha1((p.get("session_id", "") + "\x00"
                              + p.get("transcript", "") + "\x00"
                              + p.get("action", "")).encode()).hexdigest()[:16]
            if k in seen_pairs:
                stats["pairs:cross-file-dupes"] += 1
                continue
            seen_pairs.add(k)
            out_fh.write(json.dumps(p, ensure_ascii=False) + "\n")
            if p.get("secret_hits"):
                stats["pairs-written:with-secret-hits"] += 1
            if p.get("projection_approximated"):
                stats["tally:approximated"] += 1
            if p.get("session_version"):
                stats[f"ver:{p['session_version']}"] += 1
            wrote += 1
        out_fh.flush()
        n_total += wrote
        print(f"  [{i}/{len(files)}] {f.name}: +{wrote} new "
              f"(total {n_total})", flush=True)

    out_fh.close()  # rows were streamed per file; memory stays flat
    print(f"\nwrote {args.out}: {n_total} pairs")
    print("\nrecord types seen:")
    for k in sorted(stats):
        if k.startswith("rec:"):
            print(f"  {k[4:]:28} {stats[k]}")
    print("\nexport path:")
    for k in sorted(stats):
        if k.startswith(("export:", "files:")):
            print(f"  {k:28} {stats[k]}")
    print("\nskips:")
    for k in sorted(stats):
        if k.startswith("skipped:"):
            print(f"  {k[8:]:28} {stats[k]}")
    print("\ntool_use / pairs:")
    for k in sorted(stats):
        if k.startswith(("tool_use:", "pairs:", "files_", "lines_")):
            print(f"  {k:28} {stats[k]}")
    appr = stats.get("tally:approximated", 0)
    print(f"\napproximated projections: {appr}/{n_total}")
    print(f"pairs written with secret-hits: "
          f"{stats.get('pairs-written:with-secret-hits', 0)} "
          f"(metadata only; values never printed)")
    print("note: pairs:with-secret-hits above counts pre-dedup rows; "
          "the written figure is authoritative")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())