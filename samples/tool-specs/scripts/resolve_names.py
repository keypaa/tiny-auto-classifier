import os, re, glob, json

SRC = "/tmp/clsrun"
PARTS = "/tmp/rev/extract/parts"

def load(fname):
    return open(os.path.join(SRC, fname), encoding="utf-8", errors="replace").read()

def resolve_const(fname, ident, depth=0):
    """find `ident="value"` or `let ident="value"` assignment anywhere in file"""
    if depth > 3:
        return None
    s = load(fname)
    pats = [
        r'\b(?:let|var|const)\s+' + re.escape(ident) + r'\s*=\s*"((?:[^"\\]|\\.)*)"',
        r'\b' + re.escape(ident) + r'\s*=\s*"((?:[^"\\]|\\.)*)"',
        r'\b(?:let|var|const)\s+' + re.escape(ident) + r'\s*=\s*\'((?:[^\'\\]|\\.)*)\'',
        r'\b' + re.escape(ident) + r'\s*=\s*\'((?:[^\'\\]|\\.)*)\'',
    ]
    for p in pats:
        m = re.search(p, s)
        if m:
            return m.group(1)
    # maybe defined in another chunk via import
    return None

results = []
for f in sorted(glob.glob(PARTS + "/*.txt")):
    p = open(f, encoding="utf-8").read()
    m = re.search(r'FILE: (\S+)\s+OFFSET: (\d+)', p)
    fname, off = m.group(1), int(m.group(2))
    s = load(fname)
    head = s[:off]
    # find nearest enclosing spec start: pattern `({name:` or `(e)=>({name:` etc.
    best = None
    for nm in re.finditer(r'\{\s*name\s*:\s*([A-Za-z_$][\w$]*)', head):
        best = nm
    # also plain `name:"literal"`
    bestlit = None
    for nm in re.finditer(r'\{\s*name\s*:\s*"([^"]{0,60})"', head):
        bestlit = nm
    res = {"file": fname, "off": off,
           "specStartDist": (off - best.start()) if best else None,
           "nameIdent": best.group(1) if best else None}
    if best:
        res["nameValue"] = resolve_const(fname, best.group(1))
    res["nameLiteralDist"] = (off - bestlit.start()) if bestlit else None
    # aliases near spec start
    if best:
        win = head[best.start():best.start()+3000]
        al = re.search(r'aliases\s*:\s*\[([^\]]*)\]', win)
        res["aliases"] = al.group(1) if al else None
    results.append(res)

for r in results:
    print("%-24s @%-8s dist=%-6s ident=%-8s value=%-28s alias=%s" % (
        r["file"], r["off"], r["specStartDist"], r["nameIdent"],
        r.get("nameValue"), r.get("aliases")))
json.dump(results, open("/tmp/rev/extract/names.json","w"), indent=1)