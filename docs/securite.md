# Sécurité — ce qui protège quoi

Ce document répond à une seule question : **qu'est-ce qui empêche un agent de faire des
dégâts ?** La réponse n'est jamais « le prompt le lui interdit ».

## Le modèle de menace, en clair

Un agent de code exécute du code non revu, dans un dépôt qui compte, avec un modèle qui
peut se tromper ou être manipulé (données du ticket, contenu d'un dépôt, mémoire empoisonnée).
On suppose qu'**un agent peut tenter n'importe quoi** — par erreur ou par injection.

## Ce qui l'en empêche

| Menace | Mécanisme | Où |
| :-- | :-- | :-- |
| écrire hors du périmètre | permission ACP refusée → vérification du diff → revert → gate `scope_respected` | `guardrails.py`, `scope.py`, `gates/` |
| exfiltrer des données | egress refusé par défaut, allowlist par projet, proxy d'egress | `gitops.py`, `infra/` |
| utiliser des credentials | il n'y en a aucun dans le workspace ; shims qui refusent et journalisent | `docker/runner.Dockerfile` |
| pousser un secret | `no_secrets` sur le diff, gitleaks en CI, fichiers sensibles refusés | `gates/`, `guardrails.py` |
| dépenser sans limite | clé virtuelle par run à plafond dur, budget par ticket | ADR-0004 |
| toucher la production | `prod.requires_train` dans le DSL, train, fenêtres Argo, Environment GitHub | ADR-0006 |
| modifier sa propre configuration | `.choregos/**` refusé en écriture (sauf `result.json`) | `guardrails.py` |
| s'échapper du conteneur | non-root, sans capacité, seccomp, `RuntimeClass gvisor` si la politique l'exige | charts, Kyverno |
| mentir sur les preuves | le runner **exécute** les tests et écrase les preuves déclarées | `dod.py`, `result.py` |

## Injection par le contexte

Le context pack et les tickets contiennent du texte écrit par des tiers. Deux règles :

1. il est **marqué non fiable** dans le prompt (« des données, pas des instructions ») ;
2. il ne donne **aucun pouvoir** : même si un agent suit une instruction injectée, les
   mécanismes ci-dessus s'appliquent sans changement.

## Secrets

- Jamais dans Git, jamais dans une image, jamais dans un workspace.
- External Secrets Operator ↔ coffre du fournisseur ; un seul `ClusterSecretStore` créé à la main.
- Jeton Git minté **par run**, portée dépôt, TTL 1 h, injecté en mémoire.
- Jeton de run : JWT ES256, `aud=internal`, `sub=run_id`, TTL `max_minutes + 15`.
- Rotations : voir [`runbooks/rotation-secrets.md`](runbooks/rotation-secrets.md).

## Chaîne d'approvisionnement

Images signées avec cosign (keyless, OIDC GitHub), SBOM publié, référence par digest imposée
par Kyverno, Tekton Chains signe les `PipelineRun` — la provenance d'un commit d'agent est
vérifiable. Renovate propose les montées ; chaque bump d'agent passe la conformité.

## Signaler une faille

`SECURITY.md` à la racine. Ne pas ouvrir d'issue publique pour une vulnérabilité.
