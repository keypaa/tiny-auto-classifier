import os, re, glob, json, sys
sys.path.insert(0, "/tmp/rev")
import xref
from xref import resolve
from named import value, file_text

SRC = "/tmp/clsrun"

def balanced(s, i, op='{', cl='}'):
    depth = 0
    instr = None
    while i < len(s):
        c = s[i]
        if instr:
            if c == '\\':
                i += 2; continue
            if c == instr: instr = None
        else:
            if c in '"\'`': instr = c
            elif c == op: depth += 1
            elif c == cl:
                depth -= 1
                if depth == 0: return i
        i += 1
    return -1

rows = []
for f in sorted(glob.glob(SRC + "/chunk-*.js")):
    base = os.path.basename(f)
    s = file_text(base)
    for m in re.finditer(r'\bGt\(\s*\{', s):
        bo = m.end() - 1
        be = balanced(s, bo)
        if be == -1: continue
        obj = s[bo:be+1]
        nm = re.match(r'\s*\{\s*name\s*:\s*([A-Za-z_$][\w$]*|"(?:[^"\\]|\\.)*")', obj)
        if not nm: continue
        ident = nm.group(1)
        val = ident.strip('"') if ident.startswith('"') else value(base, ident)
        has = 'toAutoClassifierInput' in obj
        mth = None
        if has:
            mm = re.search(r'toAutoClassifierInput', obj)
            mth = obj[mm.start():mm.start()+180]
        al = re.search(r'aliases\s*:\s*\[([^\]]*)\]', obj)
        rows.append(dict(file=base, off=m.start(), name=val, ident=ident,
                         has=has, aliases=al.group(1) if al else None,
                         size=len(obj), snippet=mth))

print("TOTAL specs with name: %d ; with toAutoClassifierInput: %d" % (len(rows), sum(r['has'] for r in rows)))
print()
print("=== HAS toAutoClassifierInput ===")
for r in sorted(rows, key=lambda x: str(x['name'])):
    if r['has']:
        print("%-28s %-22s @%-8s size=%-7s alias=%s" % (r['name'], r['file'], r['off'], r['size'], r['aliases']))
print()
print("=== LACKS toAutoClassifierInput ===")
for r in sorted(rows, key=lambda x: str(x['name'])):
    if not r['has']:
        print("%-28s %-22s @%-8s size=%-7s alias=%s" % (r['name'], r['file'], r['off'], r['size'], r['aliases']))
json.dump(rows, open("/tmp/rev/extract/specs.json","w"), indent=1)