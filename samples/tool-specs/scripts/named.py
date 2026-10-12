import os, re, glob, json, sys
sys.path.insert(0, "/tmp/rev")
from xref import resolve, exports, imports, SRC

def file_text(fname):
    return open(os.path.join(SRC, fname), encoding="utf-8", errors="replace").read()

_cache = {}
def value(fname, local, depth=0, seen=None):
    """Return the literal string value of a constant, following cross-chunk refs."""
    if seen is None: seen = set()
    key = (fname, local)
    if key in seen or depth > 10:
        return None
    seen.add(key)
    r = resolve(fname, local)
    if r is None: return None
    f2, l2 = r
    # find assignment
    for pat in [r'\b(?:let|var|const)\s+' + re.escape(l2) + r'\s*=\s*"((?:[^"\\]|\\.)*)"',
                r'\b' + re.escape(l2) + r'\s*=\s*"((?:[^"\\]|\\.)*)"',
                r'\b(?:let|var|const)\s+' + re.escape(l2) + r"\s*=\s*'((?:[^'\\]|\\.)*)'",
                r'\b' + re.escape(l2) + r"\s*=\s*'((?:[^'\\]|\\.)*)'"]:
        s = file_text(f2)
        m = re.search(pat, s)
        if m:
            return m.group(1)
    # cross-chunk alias re-export? try value = otherIdent
    for pat in [r'\b(?:let|var|const)\s+' + re.escape(l2) + r'\s*=\s*([A-Za-z_$][\w$]*)\s*[;,]',
                r'\b' + re.escape(l2) + r'\s*=\s*([A-Za-z_$][\w$]*)\s*[;,]']:
        s = file_text(f2)
        m = re.search(pat, s)
        if m:
            return value(f2, m.group(1), depth+1, seen)
    return None

def nearest_spec_name(fname, off):
    s = file_text(fname)
    head = s[:off]
    best = None
    for nm in re.finditer(r'\{\s*name\s*:\s*([A-Za-z_$][\w$]*)', head):
        best = nm
    bestlit = None
    for nm in re.finditer(r'\{\s*name\s*:\s*"([^"]{0,60})"', head):
        bestlit = nm
    if best and (not bestlit or best.start() > bestlit.start()):
        return best.group(1), off - best.start()
    if bestlit:
        return bestlit.group(1), off - bestlit.start()
    if best:
        return best.group(1), off - best.start()
    return None, None

if __name__ == "__main__":
    rows = []
    for p in sorted(glob.glob("/tmp/rev/extract/parts/*.txt")):
        txt = open(p, encoding="utf-8").read()
        m = re.search(r'FILE: (\S+)\s+OFFSET: (\d+)', txt)
        f, off = m.group(1), int(m.group(2))
        ident, dist = nearest_spec_name(f, off)
        val = value(f, ident) if ident else None
        # aliases
        s = file_text(f)
        head = s[:off]
        al = None
        if dist and dist < 4000:
            win = head[len(head)-dist: len(head)-dist+4000]
            am = re.search(r'aliases\s*:\s*\[([^\]]*)\]', win)
            if am:
                al = am.group(1)
        rows.append({"file": f, "off": off, "ident": ident, "dist": dist,
                     "value": val, "aliases": al, "part": os.path.basename(p)})
    for r in rows:
        print("%-22s @%-8s d=%-6s %-10s => %-26s alias=%s" % (
            r["file"], r["off"], r["dist"], r["ident"], r["value"], r["aliases"]))
    json.dump(rows, open("/tmp/rev/extract/named.json","w"), indent=1)