import os, re, glob, json, sys

SRC = "/tmp/clsrun"

# 1. Build export map: file -> {exportedName: localIdent}
exports = {}
# 2. import map: file -> {localIdent: (srcfile, exportedName)}
imports = {}

for f in sorted(glob.glob(SRC + "/chunk-*.js")):
    base = os.path.basename(f)
    s = open(f, encoding="utf-8", errors="replace").read()
    ex = {}
    for m in re.finditer(r'export\s*\{([^}]*)\}', s):
        for part in m.group(1).split(','):
            part = part.strip()
            if not part:
                continue
            if ' as ' in part:
                loc, ext = part.split(' as ')
                ex[ext.strip()] = loc.strip()
            else:
                ex[part] = part
    exports[base] = ex
    im = {}
    for m in re.finditer(r'import\s*\{([^}]*)\}\s*from\s*"\./(chunk-[^"]+\.js)"', s):
        names, src = m.group(1), m.group(2)
        for part in names.split(','):
            part = part.strip()
            if not part:
                continue
            if ' as ' in part:
                ext, loc = part.split(' as ')
            else:
                ext = loc = part
            im[loc.strip()] = (src, ext.strip())
    imports[base] = im

json.dump({"exports": exports, "imports": imports}, open("/tmp/rev/extract/xref.json","w"))
print("files:", len(exports), "total exports:", sum(len(v) for v in exports.values()))

# resolve a global symbol: follow imports transitively
def resolve(fname, local, depth=0, seen=None):
    if seen is None: seen = set()
    key = (fname, local)
    if key in seen or depth > 8:
        return None
    seen.add(key)
    im = imports.get(fname, {})
    if local in im:
        sf, ext = im[local]
        if sf in exports and ext in exports[sf]:
            return resolve(sf, exports[sf][ext], depth+1, seen)
        return (sf, ext)
    return (fname, local)

if __name__ == "__main__":
    # test: Pm in chunk-b2sa2xdg.js should resolve to WebSearch
    for f, i in [("chunk-b2sa2xdg.js","Pm"),("chunk-b2sa2xdg.js","El"),("chunk-b2sa2xdg.js","Ic"),
                 ("chunk-b2sa2xdg.js","TC"),("chunk-b2sa2xdg.js","ZM")]:
        print(f, i, "->", resolve(f,i))