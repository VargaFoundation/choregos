import json, os, re, sys
from rank_bm25 import BM25Okapi
root, qpath = sys.argv[1], sys.argv[2]
docs, paths = [], []
for d, dn, fn in os.walk(root):
    dn[:] = [x for x in dn if x not in {"graphify-out", "node_modules", ".git", "fr"}]
    for f in fn:
        if f.endswith((".py", ".md", ".ts", ".tsx", ".yaml", ".yml")):
            p = os.path.join(d, f)
            try: t = open(p, errors="ignore").read()
            except OSError: continue
            rel = os.path.relpath(p, root)
            docs.append(re.findall(r"[a-z0-9]+", (rel + " " + t).lower())); paths.append(rel)
bm = BM25Okapi(docs)
out = {}
for q in json.load(open(qpath)):
    sc = bm.get_scores(re.findall(r"[a-z0-9]+", q["q"].lower()))
    top = [paths[i] for i in sorted(range(len(sc)), key=lambda i: -sc[i])[:5]]
    exp = q.get("files") or []
    adr = q.get("adr") or []
    hit_f = sum(1 for e in exp if e in top) / len(exp) if exp else None
    hit_a = sum(1 for a in adr if any(t.startswith(f"docs/adr/{a}") for t in top)) / len(adr) if adr else None
    out[q["id"]] = {"top5": top, "file_recall@5": hit_f, "adr_recall@5": hit_a}
json.dump(out, open(sys.argv[3], "w"), indent=1)
for k, v in out.items(): print(k, v["file_recall@5"], v["adr_recall@5"])
