"""Budget shim in front of `claude` for graphify's claude-cli backend: per-call cap, cumulative ledger, hard stop."""
import json, os, subprocess, sys
LEDGER = os.environ["SHIM_LEDGER"]; CAP = float(os.environ["SHIM_CAP_USD"]); REAL = os.environ["SHIM_REAL_CLAUDE"]
# recursion guard (2026-10-27): SHIM_REAL_CLAUDE=$(which claude) computed with the shim already first
# in PATH resolved to this shim; it then called itself ~1200 times deep and OOM-killed the whole WSL box.
if os.environ.get("SHIM_DEPTH") or os.path.dirname(os.path.realpath(REAL)) == os.path.dirname(os.path.realpath(sys.argv[0])) \
        or open(REAL, "rb").read(200).find(b"claude_shim.py") >= 0:
    sys.stderr.write(f"claude_shim: SHIM_REAL_CLAUDE={REAL} is the shim itself, refusing to recurse\n"); sys.exit(97)
os.environ["SHIM_DEPTH"] = "1"
spent = 0.0
if os.path.exists(LEDGER):
    spent = sum(json.loads(l)["cost"] for l in open(LEDGER) if l.strip())
args = sys.argv[1:]
if "-p" not in args and "--print" not in args:
    os.execv(REAL, [REAL, *args])  # version / help probes
if spent >= CAP:
    print(json.dumps({"type": "result", "subtype": "error", "is_error": True, "result": f"budget cap {CAP} reached ({spent:.4f})"}))
    sys.exit(1)
proc = subprocess.run([REAL, *args, "--max-budget-usd", os.environ.get("SHIM_CALL_CAP_USD", "0.20"), "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
                      stdin=sys.stdin, capture_output=True, text=True)
cost = 0.0
try:
    cost = float(json.loads(proc.stdout).get("total_cost_usd") or 0)
except Exception:
    pass
with open(LEDGER, "a") as f:
    f.write(json.dumps({"cost": cost, "rc": proc.returncode}) + "\n")
sys.stdout.write(proc.stdout); sys.stderr.write(proc.stderr)
sys.exit(proc.returncode)
