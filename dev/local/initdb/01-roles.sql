-- SPDX-License-Identifier: Apache-2.0
-- Local install (`compose.yaml`): the application role and the gateway's database.
--
-- The platform connects as a NON-superuser role: a superuser ignores row-level security,
-- whatever the policies say. Migrations run under this role too, so it owns the tables and
-- `FORCE ROW LEVEL SECURITY` applies to it (same set-up as `essai/demarrage-local`).
-- Development passwords only: this file never leaves a laptop.
CREATE ROLE choregos_app LOGIN PASSWORD 'local-dev-only' NOSUPERUSER;
GRANT ALL PRIVILEGES ON DATABASE choregos TO choregos_app;
-- LiteLLM keeps its virtual keys and spend in its own database.
CREATE DATABASE litellm OWNER choregos;
\connect choregos
GRANT USAGE, CREATE ON SCHEMA public TO choregos_app;
