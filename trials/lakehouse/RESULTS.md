# Trial, element 7 - results of 2026-10-03

**Question**: does a standard Iceberg REST catalog (Lakekeeper) on S3 storage (MinIO), written by
PyIceberg and read by DuckDB, hold ADR 0007 (Lakekeeper) and 0008 (DuckDB locally) of the requirements
document, with storage credentials limited to what the run must touch?

**Answer: yes, in STS mode.**

## Versions

| Component | Version |
|---|---|
| Lakekeeper | 0.13.6 (`quay.io/lakekeeper/catalog:latest`) |
| MinIO | RELEASE.2025-09-07T16-13-09Z (cached image, see "Risks") |
| PyIceberg | 0.10.x, with `pyarrow` 25.0.1 and `s3fs` |
| DuckDB | 1.5.6, extensions `iceberg` and `httpfs` |
| PostgreSQL (Lakekeeper's database) | 16 |

## What was proved

| # | Proof | Measure |
|---|---|---|
| 1 | PyIceberg creates the nested namespace `p_demo.health` (rule of 03 §2.2) and a table, then writes 1,000 rows to it | 2.0 s with remote signing; 0.39 s with STS |
| 2 | DuckDB attaches the REST catalog and reads back the right counts (1,000 rows, 50 keys, 667 findings), nested namespace included | 33 to 39 ms |
| 3 | **With STS, no S3 secret on the client side**: the catalog hands out temporary credentials (a session token), used by PyIceberg and by DuckDB (`ACCESS_DELEGATION_MODE 'vended_credentials'`) | - |
| 4 | **These credentials open only the table**: reading and writing in its location are allowed; listing the bucket root, writing under another prefix and reading another bucket are refused (`ACCESS_DENIED`) | `scope.py` |
| 5 | Footprint of the stack at rest | Lakekeeper 55 MiB, PostgreSQL 92 MiB, MinIO 193 MiB |

To replay: `./run.sh`, then `STS=true WAREHOUSE=trial-sts DELEGATION=vended ./run.sh` (about 75 s,
Python package installation included).

## What follows for the requirements document

- **ADR 0007 confirmed**: Lakekeeper serves as the catalog without development, and its per-table
  credential vending gives the isolation that graal's in-house facade built by hand. graal's
  commit guards become test cases (R-DAT-LAK-04).
- **ADR 0008 confirmed for reading**: DuckDB reads Iceberg through the REST catalog, nested
  namespaces included.
- **STS mode is mandatory** as soon as DuckDB reads: DuckDB cannot do remote signing
  (it would otherwise need its own S3 credentials, which the run must not hold). Lakekeeper's storage
  profile must therefore enable STS (`sts-enabled`, with an `sts-role-arn`, even a dummy one
  for MinIO). R-DAT-LAK-03: "STS first, remote signing as a fallback for the engines that
  know it".
- **Table locations are a UUID** (`warehouse-sts/<uuid>`), not the namespace's path:
  the rule "every location under the project's prefix" (R-DAT-LAK-04) goes through Lakekeeper's
  `storage-layout` option, or becomes "every table under the bucket or the prefix of the
  project's warehouse". To be settled in DAT-003.

## Risks noted

- **The MinIO image is no longer published**, neither on Docker Hub nor on quay.io (checked on 2026-10-04).
  The local start by `docker compose up` (D8) cannot depend on it: see the next section,
  which replaces it with RustFS.
- Two warehouses cannot share a bucket without a prefix (`CreateWarehouseStorageProfileOverlap`):
  one warehouse per installation (ADR 0007) allows it, with a prefix per project if needed.
- Lakekeeper ran **without authentication** in the trial. In production, it plugs into the IdP
  (OIDC) and an authorizer (OpenFGA or equivalent); that is the object of DAT-001 and DAT-003.

## Q22: RustFS instead of MinIO (2026-10-04)

**Question**: which S3 storage replaces MinIO, whose image is no longer published, without losing what
element 7 showed to be necessary, STS credentials bounded per table?

**Answer: RustFS** (`rustfs/rustfs:1.0.1`, Apache-2.0, published image). Now the default storage
of this stack (service `s3`).

| Check | Result |
|---|---|
| `AssumeRole` with a session policy limited to `projet-a/*` | temporary credentials handed out |
| Read `projet-a` with these credentials | allowed |
| Read or write `projet-b` with these credentials | refused (`AccessDenied`) |
| The whole of element 7 in STS mode: PyIceberg write, DuckDB read through vended credentials | 1,000 rows written and read back |
| Scope test (`scope.py`): its own table allowed; the bucket root, another prefix, another bucket refused | identical to MinIO |

Discarded without a trial: SeaweedFS (no STS); Garage and a MinIO image built from source
(AGPL-3.0, incompatible with a licence report free of AGPL).

What is not proved: how RustFS holds under load and over time (a young project, 1.0 in 2026),
replication and recovery. The `mc` client is still used to create the buckets: it is published, but
it is a MinIO tool (AGPL), to be replaced by a generic S3 client in the shipped stack.
