import os, re, glob

PARTS = "/tmp/rev/extract/parts"
files = sorted(glob.glob(PARTS + "/*.txt"))
for f in files:
    p = open(f, encoding="utf-8").read()
    m = re.search(r'FILE: (\S+)\s+OFFSET: (\d+)', p)
    fname, off = m.group(1), int(m.group(2))
    ctx = p.split('--- PRECEDING CONTEXT', 1)[1].split('--- METHOD ---')[0]
    ctx = ctx.split('\n', 1)[1] if '\n' in ctx else ctx
    # all name: "..." occurrences in ctx
    names = []
    for nm in re.finditer(r'\bname\s*:\s*"([^"]{0,60})"', ctx):
        names.append((len(ctx) - (nm.start()), nm.group(1)))
    names.sort()
    top = names[:4]
    # other markers
    markers = []
    for mk in ['isReadOnly', 'inputSchema', 'outputSchema', 'renderToolUseMessage', 'aliases', 'create(', 'userFacingName', 'permissionSuggestions', 'isEnabled', 'cliName']:
        c = ctx.count(mk)
        if c:
            markers.append("%s x%d" % (mk, c))
    body = p.split('--- METHOD ---', 1)[1].strip().replace('\n', ' ')
    print("### %s  @%s" % (fname, off))
    print("    names(nearest-first): %s" % [(d, n) for d, n in top])
    print("    markers: %s" % ", ".join(markers))
    print("    BODY: %s" % body[:200])
    print()