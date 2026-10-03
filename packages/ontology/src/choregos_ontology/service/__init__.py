# SPDX-License-Identifier: Apache-2.0
"""L'ontologie servie par le cœur de Choregos, comme un greffon (essai, éléments 2 et 3).

Ce sous-paquet est la partie « service » de l'essai : il parle à PostgreSQL et à l'API du cœur
(`choregos-api`, extra `service`). Le reste de `choregos_ontology` — modèle, chargement,
validation, compilation, observations — reste sans I/O.

Le greffon passe par les coutures du cœur, et par elles seules :
- `choregos.plugins` → `plugin.brancher()` déclare ses routes (`declarer_un_routeur`) et ses
  outils de run (`declarer_un_fournisseur_d_outils`) ;
- `choregos.migrations` → `plugin.MIGRATIONS`, une branche Alembic qui crée SES tables, sous RLS.

Rien n'est importé du cœur au chargement de ce module : `brancher()` importe ce qu'il faut.
"""
