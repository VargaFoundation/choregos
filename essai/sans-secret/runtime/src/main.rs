// SPDX-License-Identifier: Apache-2.0
//! Trial runtime (element 8): exchanges a single-use bootstrap code for a run token and the declared
//! secrets, then runs the command with the secrets in its environment only (never on disk).
//!
//! Usage: `runtime -- <command> [args…]`. Reads `CHOREGOS_RUN_ID`, `CHOREGOS_RUN_BOOTSTRAP_CODE` and
//! `CHOREGOS_INTERNAL_API_URL`. The code is removed from the child's environment.

use std::collections::HashMap;
use std::env;
use std::io::{BufRead, BufReader};
use std::process::{exit, Command, Stdio};
use std::thread::sleep;
use std::time::Duration;

fn fail(message: &str) -> ! {
    eprintln!("runtime: {message}");
    exit(70)
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let Some(split) = args.iter().position(|a| a == "--") else { fail("usage: runtime -- <command>") };
    let command = &args[split + 1..];
    if command.is_empty() {
        fail("no command after --");
    }
    let run_id = env::var("CHOREGOS_RUN_ID").unwrap_or_else(|_| fail("CHOREGOS_RUN_ID is missing"));
    let code = env::var("CHOREGOS_RUN_BOOTSTRAP_CODE")
        .unwrap_or_else(|_| fail("no bootstrap code: a run never starts without its single-use code"));
    let api = env::var("CHOREGOS_INTERNAL_API_URL").unwrap_or_else(|_| fail("CHOREGOS_INTERNAL_API_URL is missing"));

    // 425 means the orchestrator has not recorded this pod yet: retry for a bounded time, never forever.
    let mut attempts = 0;
    let body: serde_json::Value = loop {
        attempts += 1;
        let response = ureq::post(&format!("{api}/runs/{run_id}/bootstrap"))
            .send_json(serde_json::json!({ "code": code }));
        match response {
            Ok(r) => break r.into_json().unwrap_or_else(|e| fail(&format!("invalid bootstrap response: {e}"))),
            Err(ureq::Error::Status(425, _)) if attempts < 60 => sleep(Duration::from_secs(1)),
            Err(ureq::Error::Status(status, r)) => {
                let detail = r.into_string().unwrap_or_default();
                fail(&format!("bootstrap refused ({status}) after {attempts} attempt(s): {detail}"))
            }
            Err(e) if attempts < 10 => {
                eprintln!("runtime: API unreachable ({e}), retrying");
                sleep(Duration::from_secs(1))
            }
            Err(e) => fail(&format!("API unreachable: {e}")),
        }
    };
    println!("runtime: bootstrap after {attempts} attempt(s)");
    let token = body["token"].as_str().unwrap_or_else(|| fail("no token in the bootstrap response")).to_string();
    let secrets: HashMap<String, String> = body["secrets"]
        .as_object()
        .map(|m| m.iter().filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string()))).collect())
        .unwrap_or_default();
    println!("runtime: bootstrap ok, {} secret(s) in memory", secrets.len());

    let mut child = Command::new(&command[0])
        .args(&command[1..])
        .env_remove("CHOREGOS_RUN_BOOTSTRAP_CODE")
        .envs(&secrets)
        .stdout(Stdio::piped())
        .spawn()
        .unwrap_or_else(|e| fail(&format!("cannot start {}: {e}", command[0])));
    let mut last_line = String::new();
    if let Some(stdout) = child.stdout.take() {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            println!("{line}");
            last_line = line;
        }
    }
    let status = child.wait().unwrap_or_else(|e| fail(&format!("wait failed: {e}")));
    let exit_code = status.code().unwrap_or(1);
    let posted = ureq::post(&format!("{api}/runs/{run_id}/result"))
        .set("Authorization", &format!("Bearer {token}"))
        .send_json(serde_json::json!({ "exit_code": exit_code, "last_line": last_line }));
    if let Err(e) = posted {
        fail(&format!("result refused: {e}"));
    }
    exit(exit_code)
}
