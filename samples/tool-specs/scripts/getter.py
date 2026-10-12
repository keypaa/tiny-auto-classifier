import os, re, sys
sys.path.insert(0, "/tmp/rev")
from named import file_text

def balanced(s, i, op='{', cl='}'):
    depth = 0; instr = None
    while i < len(s):
        c = s[i]
        if instr:
            if c == '\\': i += 2; continue
            if c == instr: instr = None
        else:
            if c in '"\'`': instr = c
            elif c == op: depth += 1
            elif c == cl:
                depth -= 1
                if depth == 0: return i
        i += 1
    return -1

def method(fname, off):
    s = file_text(fname)
    i = off + len('toAutoClassifierInput')
    while s[i] in ' \t\n': i += 1
    if s[i] != '(':
        return "NOT A METHOD at %d: %s" % (i, s[i:i+60])
    pe = balanced(s, i, '(', ')')
    k = pe + 1
    while s[k] in ' \t\n': k += 1
    if s[k] != '{':
        return "NO BODY: " + s[off:pe+1]
    be = balanced(s, k)
    return s[off:be+1]

def helper(fname, ident):
    s = file_text(fname)
    for pat in [r'function %s\(' % re.escape(ident), r'\b%s\s*=\s*(?:\(|function)' % re.escape(ident),
                r'\bvar %s\s*=\s*' % re.escape(ident), r'\blet %s\s*=\s*' % re.escape(ident)]:
        m = re.search(pat, s)
        if not m: continue
        i = s.find('(', m.start())
        if i == -1 or i - m.start() > 40: continue
        pe = balanced(s, i, '(', ')')
        k = pe + 1
        while k < len(s) and s[k] in ' \t\n': k += 1
        if k < len(s) and s[k] == '{':
            be = balanced(s, k)
            return s[m.start():be+1]
        return s[m.start():m.start()+400]
    return "NOT FOUND: " + ident

if __name__ == "__main__":
    pass