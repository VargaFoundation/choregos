-- SPDX-License-Identifier: Apache-2.0
-- The application role is NOT a superuser: a superuser bypasses RLS, whatever the policies say. The
-- migrations run under this role, which therefore owns the tables: `FORCE ROW LEVEL SECURITY` applies
-- to it too. Development password only.
CREATE ROLE choregos_app LOGIN PASSWORD 'trial-dev-only' NOSUPERUSER;
GRANT ALL PRIVILEGES ON DATABASE choregos TO choregos_app;
\connect choregos
GRANT USAGE, CREATE ON SCHEMA public TO choregos_app;
