-- SPDX-License-Identifier: Apache-2.0
-- Le rôle applicatif NON superutilisateur : un superutilisateur ignore la RLS, quoi qu'écrivent les
-- politiques. Les migrations tournent sous ce rôle, qui possède donc les tables : `FORCE ROW LEVEL
-- SECURITY` s'applique à lui aussi. Mot de passe de développement seulement.
CREATE ROLE choregos_app LOGIN PASSWORD 'essai-dev-only' NOSUPERUSER;
GRANT ALL PRIVILEGES ON DATABASE choregos TO choregos_app;
\connect choregos
GRANT USAGE, CREATE ON SCHEMA public TO choregos_app;
