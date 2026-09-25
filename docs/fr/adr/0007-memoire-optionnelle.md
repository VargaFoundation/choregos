# ADR-0007 — La mémoire doit faire ses preuves avant qu'on en dépende

- **État** : acceptée
- **Concerne** : S10, S11

## Contexte

Ecphoria (mémoire bi-temporelle, base de connaissance) est prometteur et jeune : 297
commits, aucune adoption externe. Construire la plateforme dessus, c'est parier.

## Décision

1. **Périmètre réduit** : Ecphoria sert de mémoire et de base de connaissance. Sa partie
   « plateforme agentique » et son proxy auto-RAG ne sont pas utilisés (feature flags Cargo
   désactivés dans l'image `ecphoria:memory`).
2. **Repli d'égale interface** : `PgVectorMemory` implémente le même `MemoryAdapter` dans
   la base de Choregos. Passer de l'un à l'autre est une ligne de configuration.
3. **Jamais bloquant** : la lecture a un timeout court et un circuit-breaker ; une panne
   rend un **context pack vide**, jamais une erreur qui arrête une étape.
4. **Écriture gouvernée** : l'orchestrateur écrit des faits déterministes avec provenance ;
   les agents **proposent** (`propose_fact`), un humain ou une règle valide.
5. **Preuve avant dépendance** : A/B sur quatre semaines (avec et sans context pack) sur le
   taux de PR mergée au premier passage et le coût par ticket. Si la mémoire ne paie pas,
   elle reste optionnelle.

## Conséquences

- Le context pack est **marqué non fiable** dans le prompt : des données, pas des instructions.
- La matrice d'évals inclut la variante `with_memory` / `without_memory`.
- Aucun chemin critique ne dépend d'Ecphoria.
