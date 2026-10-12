import os, re, glob, json, sys
sys.path.insert(0, "/tmp/rev")
from named import value, file_text
from specs import balanced

SRC = "/tmp/clsrun"
rows = []
for f in sorted(glob.glob(SRC + "/chunk-*.js")):
    base = os.path.basename(f)
    s = file_text(base)
    for m in re.finditer(r'\bGt\(\s*\{', s):
        bo = m.end() - 1
        be = balanced(s, bo)
        if be == -1: continue
        obj = s[bo:be+1]
        head = obj[:1200]
        nm = re.search(r'\bname\s*:\s*([A-Za-z_$][\w$]*|"(?:[^"\\]|\\.)*")', head)
        if not nm: continue
        ident = nm.group(1)
        val = ident.strip('"') if ident.startswith('"') else value(base, ident)
        al = re.search(r'aliases\s*:\s*\[([^\]]*)\]', obj)
        rows.append(dict(file=base, off=m.start(), name=val, ident=ident,
                         has='toAutoClassifierInput' in obj,
                         aliases=al.group(1) if al else None))

# dedupe by (name, file, off)
seen = set(); uniq = []
for r in rows:
    k = (r['file'], r['off'])
    if k in seen: continue
    seen.add(k); uniq.append(r)

names = sorted(set(str(r['name']) for r in uniq))
print("TOTAL unique Gt specs: %d  (with toAutoClassifierInput: %d)" % (len(uniq), sum(r['has'] for r in uniq)))
print()
print("ALL TOOL NAMES (%d):" % len(names))
print(", ".join(names))
print()
want = ["Bash","Write","Edit","MultiEdit","NotebookEdit","WebFetch","WebSearch","Task","Agent",
        "KillShell","TaskStop","TodoWrite","SlashCommand","Skill","ExitPlanMode","BashOutput",
        "LS","send_message","SendMessage","ScheduleWakeup","AskUserQuestion"]
print("REQUESTED-TOOL PRESENCE CHECK:")
for w in want:
    hit = [r for r in uniq if r['name']==w or (r['aliases'] and w in r['aliases'])]
    print("  %-18s -> %s" % (w, ("spec %s @%s  hasTACI=%s alias=%s" % (hit[0]['file'],hit[0]['off'],hit[0]['has'],hit[0]['aliases'])) if hit else "NO SPEC"))
json.dump(uniq, open("/tmp/rev/extract/specs2.json","w"), indent=1)