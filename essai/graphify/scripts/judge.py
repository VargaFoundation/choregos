"""Blind judge: each answer scored 0-2 against the frozen reference, arm hidden, order shuffled."""
import glob, json, os, random, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
S = sys.argv[1]; CLAUDE = os.environ.get("CLAUDE_BIN", "claude")
qs = {q["id"]: q for q in json.load(open(f"{S}/questions.json"))}
items = []
for p in glob.glob(f"{S}/runs/*-*.r*.json"):
    name = os.path.basename(p)[:-5]; arm, rest = name.split("-", 1); qid = rest.split(".")[0]
    res = json.load(open(p)).get("result") or ""
    items.append((name, qid, res))
random.Random(7).shuffle(items)
def judge(item):
    name, qid, res = item
    out = f"{S}/judge/{name}.json"
    if os.path.exists(out): return
    q = qs[qid]
    ref = {k: q[k] for k in ("files", "adr", "fact") if k in q}
    prompt = ("You grade an answer about a software repository against a reference. Reply with ONLY a JSON object "
              '{"score": 0|1|2, "why": "<15 words>"}. 2 = correct and complete per the reference; 1 = partly correct '
              "(some expected element missing or a notable error); 0 = wrong or missing. Extra correct detail is fine.\n\n"
              f"QUESTION: {q['q']}\n\nREFERENCE: {json.dumps(ref)}\n\nANSWER:\n{res[-6000:]}")
    p = subprocess.run([CLAUDE, "-p", "--model", "haiku", "--output-format", "json", "--max-budget-usd", "0.05",
                        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--tools", ""],
                       input=prompt, capture_output=True, text=True, cwd=S)
    open(out, "w").write(p.stdout)
with ThreadPoolExecutor(3) as ex:
    list(ex.map(judge, items))
