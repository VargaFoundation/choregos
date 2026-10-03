# SPDX-License-Identifier: Apache-2.0
"""Essai, élément 7 bis : les identifiants délivrés par le catalogue n'ouvrent que leur table."""

from __future__ import annotations

import json
import sys

from pyarrow import fs
from pyiceberg.catalog import load_catalog

catalog = load_catalog("lakekeeper", type="rest", uri="http://lakekeeper:8181/catalog", warehouse="essai-sts")
table = catalog.load_table(("p_demo", "health", "findings_history"))
props = table.io.properties
s3 = fs.S3FileSystem(
    access_key=props["s3.access-key-id"], secret_key=props["s3.secret-access-key"],
    session_token=props.get("s3.session-token"), endpoint_override="minio:9000", scheme="http", region="local-01",
)
location = table.metadata.location.removeprefix("s3://")
bucket = location.split("/")[0]
report: dict[str, object] = {"location": location, "session_token": bool(props.get("s3.session-token"))}


def attempt(label: str, action) -> None:
    try:
        action()
        report[label] = "allowed"
    except OSError as error:
        report[label] = f"denied ({str(error).splitlines()[0][:90]})"


attempt("list_own_table", lambda: s3.get_file_info(fs.FileSelector(location, recursive=True)))
attempt("write_own_table", lambda: s3.open_output_stream(f"{location}/data/probe.txt").close())
attempt("list_bucket_root", lambda: s3.get_file_info(fs.FileSelector(bucket, recursive=False)))
attempt("write_elsewhere", lambda: s3.open_output_stream(f"{bucket}/autre-projet/probe.txt").close())
attempt("read_other_bucket", lambda: s3.get_file_info(fs.FileSelector("warehouse", recursive=False)))
print(json.dumps(report, indent=2))
sys.exit(0 if str(report["write_elsewhere"]).startswith("denied") else 1)
