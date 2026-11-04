#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Trial, element 7: brings the stack up, creates the warehouse, runs the PyIceberg write and the DuckDB read.
# Usage: ./run.sh (remote signing); STS=true WAREHOUSE=trial-sts DELEGATION=vended ./run.sh (STS).
# Stop: docker compose down -v
set -euo pipefail
cd "$(dirname "$0")"
STS="${STS:-false}"
WAREHOUSE="${WAREHOUSE:-trial}"
DELEGATION="${DELEGATION:-none}"
docker compose up -d --wait lakekeeper >/dev/null
for _ in $(seq 1 60); do curl -fs http://localhost:58181/health >/dev/null && break; sleep 1; done
curl -fs -X POST http://localhost:58181/management/v1/bootstrap -H 'content-type: application/json' \
  -d '{"accept-terms-of-use": true}' >/dev/null || true
BUCKET="warehouse"; ROLE=""
if [ "${STS}" = true ]; then
  BUCKET="warehouse-sts"; ROLE=', "sts-role-arn": "arn:minio:iam:::role/trial"'
  docker run --rm --network trial-lakehouse_default --entrypoint sh quay.io/minio/mc:latest \
    -c "mc alias set m http://s3:9000 trial trial-dev-only >/dev/null && mc mb -p m/${BUCKET}" >/dev/null
fi
curl -fs -X POST http://localhost:58181/management/v1/warehouse -H 'content-type: application/json' -d @- <<JSON >/dev/null || true
{"warehouse-name": "${WAREHOUSE}",
 "storage-profile": {"type": "s3", "bucket": "${BUCKET}", "endpoint": "http://s3:9000", "region": "local-01",
                     "path-style-access": true, "flavor": "s3-compat", "sts-enabled": ${STS}${ROLE}},
 "storage-credential": {"type": "s3", "credential-type": "access-key",
                        "aws-access-key-id": "trial", "aws-secret-access-key": "trial-dev-only"}}
JSON
docker run --rm --network trial-lakehouse_default -v "$PWD":/trial -w /trial -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
  -e WAREHOUSE="${WAREHOUSE}" -e DELEGATION="${DELEGATION}" python:3.12-slim \
  sh -c "pip install -q 'pyiceberg[pyarrow,s3fs]==0.10.*' duckdb 2>/dev/null; python trial.py \
         && if [ \"${DELEGATION}\" = vended ]; then python scope.py; fi"
