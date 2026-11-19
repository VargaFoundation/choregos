"""A dependency install through the proxy variables, from the agent's container (S65-04a).

Run as one of the project's checks: `pip install` of one small wheel and `npm pack` of one small
package, each timed, through whatever `HTTPS_PROXY` the check was started with (the runner's loopback
CONNECT listener under `container`). Writes one JSON report to `--out`; never fails the check.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path


def timed(command: list[str]) -> dict[str, object]:
    started = time.monotonic()
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": command[0], "ok": False, "error": str(exc)[:300]}
    return {
        "command": command[0],
        "ok": done.returncode == 0,
        "seconds": round(time.monotonic() - started, 2),
        "tail": (done.stdout + done.stderr)[-400:],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    scratch = Path(tempfile.mkdtemp(prefix="s65a-deps-"))
    report = {
        "proxy": os.environ.get("HTTPS_PROXY", ""),
        "pip": timed(
            [
                "pip",
                "install",
                "--quiet",
                "--no-cache-dir",
                "--disable-pip-version-check",
                "--target",
                str(scratch / "pip"),
                "six==1.17.0",
            ]
        ),
        "npm": timed(
            [
                "npm",
                "pack",
                "is-number@7.0.0",
                "--pack-destination",
                str(scratch),
                "--cache",
                str(scratch / "npm"),
            ]
        ),
    }
    Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    brief = {
        k: v if isinstance(v, str) else {"ok": v["ok"], "seconds": v.get("seconds")}
        for k, v in report.items()
    }
    print(json.dumps(brief))


if __name__ == "__main__":
    main()
