import os, re, glob

OUT = "/tmp/rev/extract"
txt = open(OUT + "/all-extract.txt", encoding="utf-8").read()
parts = txt.split("=" * 100)
os.makedirs(OUT + "/parts", exist_ok=True)
idx = 0
summary = []
for p in parts:
    if 'FILE:' not in p:
        continue
    idx += 1
    name = None
    m = re.search(r'FILE: (\S+)\s+OFFSET: (\d+)', p)
    fname, off = m.group(1), int(m.group(2))
    body = p.split('--- METHOD ---', 1)[1].strip()
    fn = "%02d_%s_%d.txt" % (idx, fname.replace('chunk-','').replace('.js',''), off)
    open(OUT + "/parts/" + fn, "w", encoding="utf-8").write(p)
    summary.append((idx, fname, off, len(body), body[:300].replace("\n", "\\n")))
open(OUT + "/summary.txt", "w", encoding="utf-8").write("\n".join(
    "%02d %s @%d len=%d :: %s" % s for s in summary))
print(idx, "parts")