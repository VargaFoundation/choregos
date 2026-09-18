# 4. Modèles, coûts, mémoire

## 4.1 Gateway LiteLLM (`choregos-gateway`)

Déploiement : chart LiteLLM (2 réplicas), Postgres dédié (CNPG, base `litellm`) pour clés et spend logs, Redis pour le cache et le routage, `master_key` en secret. Version **épinglée** et image vérifiée par digest (des versions compromises ont existé : ne jamais suivre `latest`).

Configuration de base (`charts/choregos/values/gateway.yaml`) :

```yaml
model_list:
  - { model_name: platform/strong,   litellm_params: { model: anthropic/claude-opus-5,     api_key: os.environ/ANTHROPIC_API_KEY } }
  - { model_name: platform/strong,   litellm_params: { model: azure_ai/claude-opus-5,      api_base: os.environ/FOUNDRY_ENDPOINT, api_key: os.environ/FOUNDRY_KEY } }
  - { model_name: platform/standard, litellm_params: { model: anthropic/claude-sonnet-5,   api_key: os.environ/ANTHROPIC_API_KEY } }
  - { model_name: platform/cheap,    litellm_params: { model: anthropic/claude-haiku-4-5,  api_key: os.environ/ANTHROPIC_API_KEY } }
  - { model_name: platform/aux,      litellm_params: { model: vertex_ai/gemini-flash-latest, vertex_project: os.environ/GCP_PROJECT } }
  - { model_name: platform/embed,    litellm_params: { model: openai/text-embedding-3-large, api_key: os.environ/OPENAI_API_KEY } }
  - { model_name: platform/local,    litellm_params: { model: openai/qwen-coder, api_base: http://vllm.choregos-gateway:8000/v1 } }
router_settings: { num_retries: 2, timeout: 600, fallbacks: [{ "platform/strong": ["platform/standard"] }] }
litellm_settings: { drop_params: true, callbacks: ["otel"], cache: true, cache_params: { type: redis } }
general_settings: { master_key: os.environ/LITELLM_MASTER_KEY, database_url: os.environ/DATABASE_URL, store_model_in_db: true }
```

Les projets **ajoutent** leurs propres entrées (`model_list` par équipe LiteLLM, via API `/model/new`) ; la plateforme expose `GET /platform/models` = `GatewayAdapter.list_models()` + prix + capacités (tool calling, vision, contexte).

Compatibilité Claude Code : le gateway sert `/v1/messages` et `/v1/messages/count_tokens` et relaie `anthropic-beta`/`anthropic-version` ; un test de la suite de conformité le vérifie à chaque montée de version.

## 4.2 Profils et résolution

```
profil demandé par l'acteur ("profile:strong", "profile:by_size")
  → project.models.profiles[name]            (ex. strong: anthropic/claude-opus-5)
  → sinon platform.models.profiles[name]     (défauts plateforme)
  → validation : le modèle est listé au gateway, supporte le tool calling, compatible avec le backend (matrice), autorisé par la politique
  → StageInput.model = { litellm_model, base_url, api_format(selon backend), params }
```

`profile:by_size` : `S,M → standard ; L → strong ; XL → strong + max_turns×1.5`. Un projet peut pointer un modèle local (`platform/local`) : le coût utilise un **prix interne** configuré (€/M tokens) pour rester comparable.

Matrice backend × modèle (`model_profiles.validated_backends`, alimentée par `EvalMatrix`) : le front refuse une combinaison non validée avec un lien vers les résultats ; l'admin peut forcer (`allow_unvalidated: true`).

## 4.3 Comptabilité des coûts

1. `mint_gateway_key` : `POST /key/generate` LiteLLM avec `max_budget = budget.usd`, `duration = max_minutes + 30m`, `models = [litellm_model]`, `metadata = {project, work_item, run_id, transition, attempt, actor, backend}` et `tags`.
2. Le backend agent reçoit **cette** clé ; LiteLLM journalise chaque requête (spend logs : tokens prompt/completion/cache, coût d'après sa table de prix ou le prix interne, latence, modèle réel après fallback) et **coupe** au plafond (l'agent reçoit une erreur budget → le runner termine `failed(reason=budget)`).
3. `collect_spend` : `GET /key/info` + `/spend/logs?request_id…` filtrés par clé → `Spend{tokens_in, tokens_out, tokens_cached, cost_usd, requests, models_used}` ; écriture `cost_ledger` (avec `fx_rate` du jour pour `cost_eur`) ; `revoke_key`.
4. Agrégats : `work_items.totals` mis à jour par run ; vues matérialisées ; `GET /costs`.
5. **Écriture dans le ticket** (`update_ticket_status_comment`) : un commentaire unique repéré par le marqueur `<!-- choregos:status -->`, réécrit à chaque étape, plus les champs structurés du board (*Coût (€)*, *Taille*, *Risque*, *Run*).

```markdown
<!-- choregos:status -->
### Choregos — suivi
**Workflow** advanced v3 · **Taille** M · **Risque** low · **État** Fini, PR ouverte · **PR** #456 · **Budget** 6,77 € / 25 €

| # | Étape | Acteur | Backend · modèle | Tokens in / out (cache) | Coût | Durée | Résultat |
|--:|:--|:--|:--|--:|--:|--:|:--|
| 1 | Relecture/complète | agent refine | openhands · claude-sonnet-5 | 41 k / 3 k (28 k) | 0,31 € | 2 min | spec ✓, 6 chemins autorisés |
| 2 | Validation | Augustin | — | — | — | 1 j 3 h | approuvée |
| 3 | En cours | agent implement | openhands · claude-sonnet-5 | 388 k / 22 k (301 k) | 2,74 € | 21 min | 7 commits |
| 4 | Vérification | agent verify | openhands · claude-sonnet-5 | 96 k / 4 k | 0,62 € | 9 min | 412 tests ✓, couverture +1,2 |
| 5 | Review | agent review | claude-code · claude-opus-5 | 120 k / 6 k | 3,10 € | 6 min | 2 remarques, corrigées |
| 6 | Review | Marie (CODEOWNERS) | — | — | — | 4 h | approuvée |
| | **Total** | | | **645 k / 35 k** | **6,77 €** | 1 j 8 h | estimé 8,20 € (médiane M) |

Findings déposés : #789 (perf, medium) · Run : https://choregos.acme.dev/p/billing-api/items/123 · Mémoire : 3 faits consultés, 1 proposé
```

Estimation avant lancement : médiane et p80 des tickets de même (projet, workflow, taille) sur 90 jours ; affichée dans le board et dans le commentaire initial ; alerte si le réel dépasse p80.

## 4.4 Ecphoria — étude

### État (README, septembre 2026)

Plateforme de mémoire agentique en un binaire Rust (Apache 2.0) : mémoires **bi-temporelles** (`valid_from`/`valid_to`), **supersession déterministe** des contradictions (historique conservé), dédup/consolidation, **recherche hybride** BM25 + vecteurs par RRF (fonctionne sans embeddings), oubli par décroissance, extraction LLM optionnelle, API compatible Mem0, **MCP natif**, protocole PostgreSQL, gRPC, Raft (3 nœuds), RBAC/JWT/OIDC, RGPD (rétention, effacement par tenant/utilisateur, sauvegarde/restauration), métriques Prometheus, SDK Python/TS/Go, intégrations LangChain/LlamaIndex, Helm. Stores : épisodique (DuckDB), mémoires (DuckDB), sémantique (USearch HNSW), état (SQLite + DashMap). Fonctions avancées : graphe de connaissances bi-temporel, provenance par fait, boucle de feedback (`helpful/wrong/obsolete`), CDC WebSocket, revue HITL des contradictions, distillation de session, traçage d'événements. Plus une « plateforme agentique » (runs durables, boucle agent, sous-agents, HITL, triggers, gateway d'outils) et un proxy LLM auto-RAG. Bench LoCoMo reproductible, bench KB jusqu'à 200 k mémoires. 297 commits, 19 PR ouvertes, pas d'adoption externe.

### Ce que Choregos lui demande

| Besoin | Fonction Ecphoria | Statut |
| :-- | :-- | :-- |
| Un tenant par projet, RBAC mappé sur les groupes OIDC | tenants + OIDC | à vérifier/aligner (tâche E-03) |
| Faits typés avec `subject` normalisé (décision, convention, incident, résumé de ticket, leçon de run, test flaky, hotspot) | mémoires + supersession | schéma métier à ajouter (E-04) |
| « Context pack » en un appel : top-k mémoires + incidents sur des chemins + tickets liés, sous budget de tokens | recherche hybride, graphe | endpoint composite à ajouter (E-05) |
| Ingestion idempotente depuis GitHub/GitLab/Jira/Argo/Tekton/Alertmanager | ingest d'événements | connecteurs à écrire (E-06, côté Choregos `MemoryIngestion` + côté Ecphoria upsert par `external_id`) |
| Faits proposés par des agents en file de validation | revue HITL des contradictions | généraliser en file `pending` (E-07) |
| Embeddings via endpoint OpenAI-compatible (LiteLLM) | Ollama / OpenAI | généraliser `ECPHORIA_EMBEDDING__BASE_URL` (E-08) |
| Lecture < 300 ms p95, écriture concurrente modérée | DuckDB embarqué, Raft | bench sur notre profil (E-09) |
| Provenance obligatoire | provenance | déjà là ; rendre obligatoire par tenant (E-10) |
| Exploitation K8s : Helm dans notre umbrella, sauvegardes, alertes | Helm, backup/restore, Prometheus | intégrer et durcir (E-11) |

### Décisions

1. **Ecphoria = mémoire + base de connaissance.** La « plateforme agentique » et le proxy auto-RAG ne sont pas utilisés par Choregos et sortent du binaire par défaut (feature flags Cargo `agentic`, `llm-proxy` désactivés dans l'image `ecphoria:memory`). Ils restent maintenables séparément si un autre usage les justifie.
2. **Pull, pas push** : les agents consultent la mémoire via MCP (`search_memory`, `get_context`) ; l'orchestrateur injecte un context pack borné et **marqué non fiable** ; jamais dans le message système.
3. **Écriture gouvernée** : l'orchestrateur écrit des faits déterministes (fin de ticket, rollback, finding créé, leçon de run) avec provenance ; les agents **proposent** (`propose_fact`) ; un humain (ou une règle : fait confirmé par ≥ 2 runs) valide.
4. **Preuve avant dépendance** : A/B sur 4 semaines (projets avec/sans context pack) sur le taux de PR mergée au premier passage et le coût par ticket ; si la mémoire ne paie pas, elle reste optionnelle.

### Améliorations à implémenter dans `VargaFoundation/ecphoria` (flux S11)

| ID | Tâche | Détail | Taille |
| :-- | :-- | :-- | :-- |
| E-01 | Triage des 19 PR ouvertes | Pour chaque PR : (a) CI verte ? (b) périmètre mémoire/KB/ops → **merger** ; périmètre plateforme agentique/proxy → **parquer** (label `deferred/agentic`) ; (c) conflits → rebase par un agent, une PR à la fois ; (d) PR de dépendances → merger si tests OK. Produire `docs/PR-TRIAGE-2026-09.md` | M |
| E-02 | Feature flags Cargo | `agentic`, `llm-proxy` optionnels ; image `ecphoria:memory` sans ; image `ecphoria:full` avec ; docs | M |
| E-03 | Tenant = projet | Création/suppression de tenant par API admin ; mapping groupes OIDC → rôles par tenant ; test d'isolation | M |
| E-04 | Schémas de faits métier | `kind` enum + validation JSON Schema par kind ; `subject` normalisé (`decision:<area>:<slug>`, `incident:<service>:<date>`, `flaky_test:<path>::<name>`…) ; migration ; docs | M |
| E-05 | Endpoint `POST /api/v1/context-pack` | `{ query, paths[], kinds[], budget_tokens, k }` → `{ memories[], incidents[], related_items[], tokens_estimated, provenance }` ; ordre par RRF puis fraîcheur ; troncature au budget ; test de latence | M |
| E-06 | Upsert idempotent | `external_id` + `source` uniques ; `PUT /memories/by-external-id` ; utilisé par les connecteurs Choregos | S |
| E-07 | File `pending` généralisée | `POST /memories?status=pending` ; `GET /pending` ; `POST /pending/{id}/accept|reject` ; règle auto-accept configurable ; événement CDC | M |
| E-08 | Embeddings OpenAI-compatible | `ECPHORIA_EMBEDDING__PROVIDER=openai_compatible`, `BASE_URL`, `MODEL`, dims auto ; test contre LiteLLM | S |
| E-09 | Bench profil Choregos | Corpus : 20 k faits, 200 k événements ; 50 lectures/s, 5 écritures/s ; mesurer p50/p95, mémoire, comportement Raft sur perte d'un nœud ; rapport | M |
| E-10 | Provenance obligatoire par tenant | Option `require_provenance: true` ; refus 422 sinon | S |
| E-11 | Chart Helm durci | `StatefulSet` 3 réplicas Raft, PDB, `NetworkPolicy`, probes, `PodSecurity` restricted, sauvegarde `CronJob` vers S3/Blob avec manifeste d'intégrité, restauration documentée, `ServiceMonitor`, alertes de base | M |
| E-12 | Releases | Semver, `cargo-release`, changelog (`cliff` déjà présent), images multi-arch signées (cosign), SBOM | S |
| E-13 | Sécurité | Revue du wire PostgreSQL (auth, limites), fuzz des parseurs d'ingestion, `cargo audit`/`deny` en CI (déjà), `SECURITY.md` à jour | M |
| E-14 | Docs d'intégration Choregos | `docs/integrations/choregos.md` : MCP, context pack, ingestion, schémas | S |

Ordre : E-01 → E-02 → E-06/E-08 (déblocages) → E-03/E-04/E-05 (cœur) → E-07/E-10 → E-11/E-12 → E-09 → E-13/E-14. E-01 peut commencer le jour 1, en parallèle de tout le reste.

### Intégration côté Choregos (flux S10)

| Composant | Détail |
| :-- | :-- |
| `packages/adapters/memory/ecphoria.py` | `MemoryAdapter` via REST + MCP ; tenant = `project.slug` ; timeouts courts (300 ms lecture, 2 s écriture) ; circuit-breaker → context pack vide plutôt qu'un stage bloqué |
| `MemoryIngestion` (Temporal, cron 15 min + événements) | tracker : tickets fermés, PR fusionnées (titre, description, résumé de review) → `ticket_summary` ; SCM : `docs/adr/*.md`, `docs/**` → `decision`/`convention` ; CD : déploiements, rollbacks → `incident` ; Alertmanager → `incident` ; orchestrateur : fin de run → `run_lesson` (échec CI récurrent, test flaky, temps par étape) ; findings créés → `finding` |
| `build_context_pack` (activité) | Requête = titre + spec ; `paths` = `allowed_paths` ; `kinds` par rôle (`refine`: decision, incident, ticket_summary ; `implement`: convention, run_lesson, hotspot ; `review`: incident, hotspot, decision) ; budget par rôle (2 k / 4 k / 3 k tokens) ; stocké avec le run |
| MCP mémoire dans le workspace | Serveur MCP Ecphoria monté en lecture (`search_memory`, `get_memory_history`) + `propose_fact` via `choregos-tools` |
| Front `/memory` | Recherche, historique bi-temporel, file `pending`, réimport |
| Éval | `EvalMatrix` inclut la variante `with_memory` / `without_memory` sur les tickets de référence |

### Repli

`packages/adapters/memory/pgvector.py` : faits dans `memory_facts(project_id, kind, subject, content, embedding, valid_from, valid_to, provenance)` avec supersession par `subject` en trigger SQL ; recherche `ts_rank` + cosinus. Moins malin, zéro service de plus ; même interface.
