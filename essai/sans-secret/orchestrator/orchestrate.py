# SPDX-License-Identifier: Apache-2.0
"""Trial orchestrator (element 8): creates a run Job without any Secret, records its pod IP, then
replays the code to check the replay rule. Prints a JSON report; exit 0 only if every check holds."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request

from kubernetes import client, config, watch

API = "http://api.essai-control.svc:8080"
RUN_NAMESPACE = "essai-run"
INTERNAL = {"x-internal-token": os.environ["ESSAI_INTERNAL_TOKEN"], "content-type": "application/json"}


def call(method: str, path: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(API + path, data=json.dumps(body or {}).encode(), method=method,
                                     headers=headers or {"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 — in-cluster URL
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def run_job(batch: client.BatchV1Api, core: client.CoreV1Api, run_id: str, code: str, command: list[str]) -> dict:
    call("POST", "/internal/runs", {"run_id": run_id, "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
                                    "secrets": ["DEMO_TOKEN"]}, INTERNAL)
    security = client.V1SecurityContext(
        run_as_non_root=True, run_as_user=10001, allow_privilege_escalation=False, read_only_root_filesystem=True,
        capabilities=client.V1Capabilities(drop=["ALL"]),
        seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"))
    container = client.V1Container(
        name="run", image="essai-run:latest", image_pull_policy="Never", args=["--", *command],
        env=[client.V1EnvVar(name="CHOREGOS_RUN_ID", value=run_id),
             client.V1EnvVar(name="CHOREGOS_RUN_BOOTSTRAP_CODE", value=code),
             client.V1EnvVar(name="CHOREGOS_INTERNAL_API_URL", value=API)],
        security_context=security)
    job = client.V1Job(
        metadata=client.V1ObjectMeta(name=run_id, labels={"essai-run": run_id}),
        spec=client.V1JobSpec(backoff_limit=0, ttl_seconds_after_finished=600, template=client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(labels={"essai-run": run_id}),
            spec=client.V1PodSpec(restart_policy="Never", automount_service_account_token=False,
                                  containers=[container]))))
    batch.create_namespaced_job(RUN_NAMESPACE, job)
    recorded = False
    for event in watch.Watch().stream(core.list_namespaced_pod, RUN_NAMESPACE, label_selector=f"essai-run={run_id}",
                                      timeout_seconds=180):
        pod = event["object"]
        if not recorded and pod.status.pod_ip and pod.status.start_time:
            call("POST", f"/internal/runs/{run_id}/pod",
                 {"ip": pod.status.pod_ip, "start_time": pod.status.start_time.timestamp()}, INTERNAL)
            recorded = True
        if pod.status.phase in {"Succeeded", "Failed"}:
            break
    logs = core.read_namespaced_pod_log(pod.metadata.name, RUN_NAMESPACE)
    _, state = call("GET", f"/internal/runs/{run_id}", headers=INTERNAL)
    return {"pod_phase": pod.status.phase, "pod_ip": pod.status.pod_ip, "logs": logs.strip().splitlines(),
            "state": state}


def main() -> int:
    config.load_incluster_config()
    batch, core = client.BatchV1Api(), client.CoreV1Api()
    report: dict[str, object] = {}
    run_id, code = f"essai-{secrets.token_hex(4)}", secrets.token_urlsafe(32)
    report["run"] = run_job(batch, core, run_id, code,
                            ["sh", "-c", 'test -n "$DEMO_TOKEN" && echo "secret received, length ${#DEMO_TOKEN}"'])
    # The orchestrator's pod has another IP: a replay of the consumed code is refused and fails the run.
    status, body = call("POST", f"/runs/{run_id}/bootstrap", {"code": code})
    _, after = call("GET", f"/internal/runs/{run_id}", headers=INTERNAL)
    report["replay"] = {"status": status, "body": body, "run_after": after.get("failure")}
    status, _ = call("POST", f"/runs/{run_id}/bootstrap", {"code": "forged"})
    report["forged_code"] = status
    late_id, late_code = f"essai-{secrets.token_hex(4)}", secrets.token_urlsafe(32)
    call("POST", "/internal/runs", {"run_id": late_id, "code_sha256": hashlib.sha256(late_code.encode()).hexdigest(),
                                    "secrets": []}, INTERNAL)
    call("POST", f"/internal/runs/{late_id}/pod", {"ip": "10.0.0.1", "start_time": time.time() - 660}, INTERNAL)
    report["expired_code"] = call("POST", f"/runs/{late_id}/bootstrap", {"code": late_code})[0]
    stolen_id, stolen_code = f"essai-{secrets.token_hex(4)}", secrets.token_urlsafe(32)
    call("POST", "/internal/runs", {"run_id": stolen_id,
                                    "code_sha256": hashlib.sha256(stolen_code.encode()).hexdigest(), "secrets": []},
         INTERNAL)
    call("POST", f"/internal/runs/{stolen_id}/pod", {"ip": "10.0.0.1", "start_time": time.time()}, INTERNAL)
    report["other_ip"] = call("POST", f"/runs/{stolen_id}/bootstrap", {"code": stolen_code})[0]
    checks = {
        "run_succeeded": report["run"]["state"].get("status") == "succeeded",
        "secret_delivered": any("secret received" in line for line in report["run"]["logs"]),
        "replay_409_and_failed": report["replay"]["status"] == 409 and report["replay"]["run_after"] == "bootstrap_replayed",
        "forged_401": report["forged_code"] == 401,
        "expired_410": report["expired_code"] == 410,
        "other_ip_403": report["other_ip"] == 403,
    }
    report["checks"] = checks
    print(json.dumps(report, indent=2, default=str), flush=True)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
