#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Essai, élément 7 : monte la pile, crée l'entrepôt, lance l'écriture PyIceberg et la lecture DuckDB.
# Usage : ./run.sh (signature distante) ; STS=true WAREHOUSE=essai-sts DELEGATION=vended ./run.sh (STS).
# Arrêt : docker compose down -v
set -euo pipefail
cd "$(dirname "$0")"
STS="${STS:-false}"
WAREHOUSE="${WAREHOUSE:-essai}"
DELEGATION="${DELEGATION:-none}"
docker compose up -d --wait lakekeeper >/dev/null
for _ in $(seq 1 60); do curl -fs http://localhost:58181/health >/dev/null && break; sleep 1; done
curl -fs -X POST http://localhost:58181/management/v1/bootstrap -H 'content-type: application/json' \
  -d '{"accept-terms-of-use": true}' >/dev/null || true
BUCKET="warehouse"; ROLE=""
if [ "${STS}" = true ]; then
  BUCKET="warehouse-sts"; ROLE=', "sts-role-arn": "arn:minio:iam:::role/essai"'
  docker run --rm --network essai-lakehouse_default --entrypoint sh quay.io/minio/mc:latest \
    -c "mc alias set m http://s3:9000 essai essai-dev-only >/dev/null && mc mb -p m/${BUCKET}" >/dev/null
fi
curl -fs -X POST http://localhost:58181/management/v1/warehouse -H 'content-type: application/json' -d @- <<JSON >/dev/null || true
{"warehouse-name": "${WAREHOUSE}",
 "storage-profile": {"type": "s3", "bucket": "${BUCKET}", "endpoint": "http://s3:9000", "region": "local-01",
                     "path-style-access": true, "flavor": "s3-compat", "sts-enabled": ${STS}${ROLE}},
 "storage-credential": {"type": "s3", "credential-type": "access-key",
                        "aws-access-key-id": "essai", "aws-secret-access-key": "essai-dev-only"}}
JSON
docker run --rm --network essai-lakehouse_default -v "$PWD":/essai -w /essai -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
  -e WAREHOUSE="${WAREHOUSE}" -e DELEGATION="${DELEGATION}" python:3.12-slim \
  sh -c "pip install -q 'pyiceberg[pyarrow,s3fs]==0.10.*' duckdb 2>/dev/null; python essai.py \
         && if [ \"${DELEGATION}\" = vended ]; then python portee.py; fi"
