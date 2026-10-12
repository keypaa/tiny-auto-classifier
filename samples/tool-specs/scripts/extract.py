import os, re, sys, glob

SRC = "/tmp/clsrun"
OUT = "/tmp/rev/extract"
os.makedirs(OUT, exist_ok=True)

def find_body(s, start):
    # start = index of '(' after the identifier
    i = start
    depth = 0
    while i < len(s):
        c = s[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1

def extract_block(s, brace_idx):
    depth = 0
    i = brace_idx
    instr = None
    while i < len(s):
        c = s[i]
        if instr:
            if c == '\\':
                i += 2
                continue
            if c == instr:
                instr = None
        else:
            if c in '"\'`':
                instr = c
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1

files = sorted(glob.glob(SRC + "/chunk-*.js"))
out = []
for f in files:
    s = open(f, encoding="utf-8", errors="replace").read()
    if 'toAutoClassifierInput' not in s:
        continue
    base = os.path.basename(f)
    for m in re.finditer(r'toAutoClassifierInput', s):
        off = m.start()
        ctx_start = max(0, off - 6000)
        ctx = s[ctx_start:off]
        # method body: after ident, optional getter syntax, find (
        j = off + len('toAutoClassifierInput')
        while j < len(s) and s[j] in ' \t\n':
            j += 1
        if j < len(s) and s[j] == '(':
            paren_end = find_body(s, j)
            if paren_end == -1:
                continue
            k = paren_end + 1
            while k < len(s) and s[k] in ' \t\n':
                k += 1
            if k < len(s) and s[k] == '{':
                bend = extract_block(s, k)
                if bend == -1:
                    continue
                body = s[off:bend+1]
            else:
                body = s[off:paren_end+1] + " <NO BODY>"
        elif j < len(s) and s[j] == '{':
            bend = extract_block(s, j)
            body = s[off:bend+1]
        else:
            body = s[off:off+120] + " <NOT A METHOD>"
        out.append("=" * 100)
        out.append("FILE: %s   OFFSET: %d" % (base, off))
        out.append("--- PRECEDING CONTEXT (%d chars) ---" % (off - ctx_start))
        out.append(ctx)
        out.append("--- METHOD ---")
        out.append(body)
        out.append("")

open(OUT + "/all-extract.txt", "w", encoding="utf-8").write("\n".join(out))
print("wrote", OUT + "/all-extract.txt", len("\n".join(out)))