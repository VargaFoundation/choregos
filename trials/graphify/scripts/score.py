"""Score agent runs against the frozen questions: file/ADR recall from the FILES:/ADR: lines, cost, turns, time."""
import glob, json, os, re, statistics as st, sys
S = sys.argv[1]
qs = {q["id"]: q for q in json.load(open(f"{S}/questions.json"))}
rows = []
for p in sorted(glob.glob(f"{S}/runs/*.json")):
    name = os.path.basename(p)[:-5]
    arm, rest = name.split("-", 1)
    qid = rest.split(".")[0]
    if qid not in qs or ".r" not in rest: continue
    try: d = json.load(open(p))
    except Exception: rows.append({"arm": arm, "qid": qid, "ok": False}); continue
    res = d.get("result") or ""
    m = re.search(r"FILES:(.*)", res); files = re.findall(r"[\w./-]+\.\w+", m.group(1)) if m else []
    files = [f.strip("`").lstrip("./") for f in files]
    m = re.search(r"ADR:(.*)", res); adrs = re.findall(r"\d{4}", m.group(1)) if m else []
    q = qs[qid]
    exp_f, exp_a = q.get("files") or [], q.get("adr") or []
    fr = sum(1 for e in exp_f if any(f.endswith(e) or e.endswith(f) and "/" in f for f in files)) / len(exp_f) if exp_f else None
    ar = sum(1 for a in exp_a if a in adrs) / len(exp_a) if exp_a else None
    prec = (sum(1 for f in files if any(f.endswith(e) or e.endswith(f) for e in exp_f)) / len(files)) if exp_f and files else None
    rows.append({"arm": arm, "qid": qid, "kind": q["kind"], "ok": d.get("subtype") == "success", "file_recall": fr, "adr_recall": ar,
                 "file_precision": prec, "cost": d.get("total_cost_usd") or 0, "turns": d.get("num_turns"), "ms": d.get("duration_ms")})
json.dump(rows, open(f"{S}/scores.json", "w"), indent=1)
def mean(xs):
    xs = [x for x in xs if x is not None]; return round(st.mean(xs), 3) if xs else None
arms = sorted({r["arm"] for r in rows})
print("arm kind n ok file_recall adr_recall file_prec cost_total turns_mean sec_mean")
for a in arms:
    for k in ("locate", "why", "trace", "all"):
        rs = [r for r in rows if r["arm"] == a and (k == "all" or r.get("kind") == k)]
        if not rs: continue
        print(a, k, len(rs), sum(r["ok"] for r in rs), mean([r.get("file_recall") for r in rs]), mean([r.get("adr_recall") for r in rs]),
              mean([r.get("file_precision") for r in rs]), round(sum(r.get("cost") or 0 for r in rs), 3),
              mean([r.get("turns") for r in rs]), mean([(r.get("ms") or 0) / 1000 for r in rs]))
