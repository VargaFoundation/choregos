# SPDX-License-Identifier: Apache-2.0
"""Trial, element 7: PyIceberg writes the history of the findings, DuckDB reads it back through the REST catalog.

Run in a container of the Compose network (see run.sh). Prints a JSON report on the output.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta

import duckdb
import pyarrow as pa
from pyiceberg.catalog import load_catalog

CATALOG_URI = "http://lakekeeper:8181/catalog"
WAREHOUSE = os.environ.get("WAREHOUSE", "trial")
NAMESPACE = ("p_demo", "health")  # the project root, then the schema (rule of 03 §2.2)
TABLE = "findings_history"
S3 = {"endpoint": "s3:9000", "key": "trial", "secret": "trial-dev-only"}


def main() -> int:
    report: dict[str, object] = {"versions": {"duckdb": duckdb.__version__, "pyarrow": pa.__version__}}
    catalog = load_catalog("lakekeeper", type="rest", uri=CATALOG_URI, warehouse=WAREHOUSE)
    catalog.create_namespace_if_not_exists(NAMESPACE[:1])
    catalog.create_namespace_if_not_exists(NAMESPACE)
    schema = pa.schema(
        [("key", pa.string()), ("layer", pa.string()), ("check", pa.string()), ("status", pa.string()),
         ("observed_at", pa.timestamp("us"))]
    )
    table = catalog.create_table_if_not_exists((*NAMESPACE, TABLE), schema=schema)
    start = datetime(2026, 10, 1)
    rows = [
        {"key": f"layer{k % 5}-check{k}", "layer": f"layer{k % 5}", "check": f"check{k}",
         "status": "finding" if (k + day) % 3 else "ok", "observed_at": start + timedelta(hours=day)}
        for k in range(50) for day in range(20)
    ]
    begin = time.perf_counter()
    table.append(pa.Table.from_pylist(rows, schema=schema))
    report["write"] = {"rows": len(rows), "seconds": round(time.perf_counter() - begin, 3)}
    report["location"] = table.metadata.location

    connection = duckdb.connect()
    for statement in ("INSTALL iceberg", "LOAD iceberg", "INSTALL httpfs", "LOAD httpfs"):
        connection.execute(statement)
    delegation = os.environ.get("DELEGATION", "none")
    report["delegation"] = delegation
    if delegation == "none":
        # DuckDB does not do remote signing: without STS it needs its own S3 credentials.
        connection.execute(
            f"CREATE SECRET s3 (TYPE S3, KEY_ID '{S3['key']}', SECRET '{S3['secret']}', "
            f"ENDPOINT '{S3['endpoint']}', URL_STYLE 'path', USE_SSL false, REGION 'local-01')"
        )
        candidates = [f"TYPE ICEBERG, ENDPOINT '{CATALOG_URI}', AUTHORIZATION_TYPE 'none', ACCESS_DELEGATION_MODE 'none'"]
    else:
        # With STS, the catalog hands out temporary credentials limited to the table: no S3 secret here.
        candidates = [f"TYPE ICEBERG, ENDPOINT '{CATALOG_URI}', AUTHORIZATION_TYPE 'none', "
                      "ACCESS_DELEGATION_MODE 'vended_credentials'"]
    begin = time.perf_counter()
    attached = None
    errors = []
    for options in candidates:
        try:
            connection.execute(f"ATTACH '{WAREHOUSE}' AS lake ({options})")
            attached = options
            break
        except duckdb.Error as error:
            errors.append(str(error).splitlines()[0])
    report["attach"] = {"options": attached, "errors": errors}
    if attached is None:
        print(json.dumps(report, indent=2))
        return 1
    query = (
        'SELECT count(*) AS row_count, count(DISTINCT key) AS keys, '
        "sum(CASE WHEN status = 'finding' THEN 1 ELSE 0 END) AS findings "
        f'FROM lake."{".".join(NAMESPACE)}".{TABLE}'
    )
    try:
        result = connection.execute(query).fetchone()
        report["read"] = {"query": query, "result": result, "seconds": round(time.perf_counter() - begin, 3)}
    except duckdb.Error as error:
        report["read"] = {"query": query, "error": str(error).splitlines()[0]}
        print(json.dumps(report, indent=2, default=str))
        return 1
    print(json.dumps(report, indent=2, default=str))
    return 0 if result and result[0] == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
