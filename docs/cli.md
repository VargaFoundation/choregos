# CLI reference

Generated from the CLI itself by `tools/gen_cli_reference.py` — do not edit by hand
(`make docs-cli` regenerates it, a test checks it is current).

The profile lives in `~/.config/choregos/config.json` (`CHOREGOS_CONFIG` overrides the
path): `api_url`, `token`, `org`. `choregos login` writes it; `CHOREGOS_API_URL`,
`CHOREGOS_TOKEN` and `CHOREGOS_ORG` are read when no profile exists. Help strings are in
French, like the rest of the code.


## `choregos dev`

Environnement de développement

### `choregos dev demo`

```text
Usage: choregos dev demo [OPTIONS]

  Joue la démonstration hors ligne (aucun service requis).

Options:
  --help  Show this message and exit.
```

### `choregos dev down`

```text
Usage: choregos dev down [OPTIONS]

Options:
  --help  Show this message and exit.
```

### `choregos dev seed`

```text
Usage: choregos dev seed [OPTIONS]

  Peuple l'environnement local : organisation, projet de démonstration,
  tickets.

Options:
  --help  Show this message and exit.
```

### `choregos dev up`

```text
Usage: choregos dev up [OPTIONS]

  Monte l'environnement de développement (kind + Tilt).

Options:
  --help  Show this message and exit.
```

## `choregos findings`

Findings

### `choregos findings action`

```text
Usage: choregos findings action [OPTIONS] {finding_id} {action}

Arguments:
  finding_id  [required]
  action      create_ticket|mark_duplicate|dismiss|agent_ready  [required]

Options:
  --help  Show this message and exit.
```

### `choregos findings list`

```text
Usage: choregos findings list [OPTIONS] {project}

Arguments:
  project  [required]

Options:
  --status <str>
  --help          Show this message and exit.
```

## `choregos items`

Tickets

### `choregos items action`

```text
Usage: choregos items action [OPTIONS] {item_id} {action}

Arguments:
  item_id  [required]
  action   pause|resume|stop|rerun_stage|mark_agent_ready  [required]

Options:
  --help  Show this message and exit.
```

### `choregos items answer`

```text
Usage: choregos items answer [OPTIONS] {item_id} {answer}

Arguments:
  item_id  [required]
  answer   [required]

Options:
  --help  Show this message and exit.
```

### `choregos items approve`

```text
Usage: choregos items approve [OPTIONS] {item_id}

Arguments:
  item_id  [required]

Options:
  --reason <str>
  --help          Show this message and exit.
```

### `choregos items create`

```text
Usage: choregos items create [OPTIONS] {project}

  Pose une demande dans Choregos (projet à tracker interne) et démarre son
  interpréteur.

Arguments:
  project  [required]

Options:
  --title <str>  [required]
  --body <str>
  --size <str>   S, M, L ou XL
  --no-start     poser sans démarrer l'interpréteur
  --help         Show this message and exit.
```

### `choregos items list`

```text
Usage: choregos items list [OPTIONS] {project}

Arguments:
  project  [required]

Options:
  --state <str>
  --help         Show this message and exit.
```

### `choregos items reject`

```text
Usage: choregos items reject [OPTIONS] {item_id}

Arguments:
  item_id  [required]

Options:
  --reason <str>  [required]
  --help          Show this message and exit.
```

### `choregos items show`

```text
Usage: choregos items show [OPTIONS] {item_id}

  Détail d'un ticket : état, coûts, timeline.

Arguments:
  item_id  [required]

Options:
  --help  Show this message and exit.
```

### `choregos login`

```text
Usage: choregos login [OPTIONS]

  Enregistre le profil de connexion (`~/.config/choregos/config.json`).

Options:
  --api-url <str>  [default: http://localhost:8000]
  --token <str>
  --org <str>      [default: varga]
  --help           Show this message and exit.
```

## `choregos orgs`

Organisations

### `choregos orgs create`

```text
Usage: choregos orgs create [OPTIONS] {slug}

  Crée une organisation (demande d'être déjà admin d'une organisation).

Arguments:
  slug  [required]

Options:
  --name <str>
  --help        Show this message and exit.
```

### `choregos orgs list`

```text
Usage: choregos orgs list [OPTIONS]

Options:
  --help  Show this message and exit.
```

## `choregos projects`

Projets

### `choregos projects create`

```text
Usage: choregos projects create [OPTIONS] {slug}

  Crée un projet ; `--template` déclenche ensuite le provisioning.

  Sans `--repo`, le projet n'a pas de dépôt (ADR 0012) : l'agent travaille
  dans un répertoire vide, et les demandes se posent avec `choregos items
  create`.

Arguments:
  slug  [required]

Options:
  --repo <str>      URL du dépôt applicatif (facultatif : un métier sans code
                    n'en a pas)
  --name <str>
  --template <str>
  --org <str>
  --language <str>  [default: python]
  --tracker <str>   `internal` : les demandes se posent dans Choregos
                    [default: internal]
  --help            Show this message and exit.
```

### `choregos projects list`

```text
Usage: choregos projects list [OPTIONS]

Options:
  --org <str>
  --help       Show this message and exit.
```

### `choregos projects provision`

```text
Usage: choregos projects provision [OPTIONS] {project}

  Lance (ou relance) le provisioning, et le suit en direct avec `--follow`.

Arguments:
  project  [required]

Options:
  --follow
  --help    Show this message and exit.
```

## `choregos runs`

Runs

### `choregos runs diff`

```text
Usage: choregos runs diff [OPTIONS] {run_id}

Arguments:
  run_id  [required]

Options:
  --help  Show this message and exit.
```

### `choregos runs list`

```text
Usage: choregos runs list [OPTIONS] {item_id}

Arguments:
  item_id  [required]

Options:
  --help  Show this message and exit.
```

### `choregos runs tail`

```text
Usage: choregos runs tail [OPTIONS] {run_id}

  Suit le journal ACP d'un run en direct (SSE).

Arguments:
  run_id  [required]

Options:
  --help  Show this message and exit.
```

## `choregos tokens`

Jetons d'API

### `choregos tokens create`

```text
Usage: choregos tokens create [OPTIONS]

  Émet un jeton d'API pour l'appelant. Le clair n'est affiché qu'ICI, une
  fois.

Options:
  --name <str>             [required]
  --expires-in-days <int>  [default: 90]
  --help                   Show this message and exit.
```

### `choregos tokens list`

```text
Usage: choregos tokens list [OPTIONS]

Options:
  --help  Show this message and exit.
```

### `choregos tokens revoke`

```text
Usage: choregos tokens revoke [OPTIONS] {token_id}

Arguments:
  token_id  [required]

Options:
  --help  Show this message and exit.
```

## `choregos trains`

Release trains

### `choregos trains approve`

```text
Usage: choregos trains approve [OPTIONS] {release_id}

Arguments:
  release_id  [required]

Options:
  --note <str>
  --help        Show this message and exit.
```

### `choregos trains depart`

```text
Usage: choregos trains depart [OPTIONS] {project}

Arguments:
  project  [required]

Options:
  --env <str>  [default: prod]
  --help       Show this message and exit.
```

### `choregos trains freeze`

```text
Usage: choregos trains freeze [OPTIONS] {project}

Arguments:
  project  [required]

Options:
  --reason <str>  [required]
  --env <str>     [default: prod]
  --help          Show this message and exit.
```

### `choregos trains status`

```text
Usage: choregos trains status [OPTIONS] {project}

Arguments:
  project  [required]

Options:
  --env <str>  [default: prod]
  --help       Show this message and exit.
```

### `choregos trains unfreeze`

```text
Usage: choregos trains unfreeze [OPTIONS] {project}

Arguments:
  project  [required]

Options:
  --env <str>  [default: prod]
  --help       Show this message and exit.
```

### `choregos whoami`

```text
Usage: choregos whoami [OPTIONS]

  Affiche l'utilisateur courant et ses rôles.

Options:
  --help  Show this message and exit.
```

## `choregos workflow`

Workflows

### `choregos workflow show`

```text
Usage: choregos workflow show [OPTIONS] [path]

  Affiche le workflow : états, transitions, ou diagramme Mermaid.

Arguments:
  path  [default: .choregos/workflow.yaml]

Options:
  --mermaid
  --help     Show this message and exit.
```

### `choregos workflow templates`

```text
Usage: choregos workflow templates [OPTIONS]

Options:
  --help  Show this message and exit.
```

### `choregos workflow validate`

```text
Usage: choregos workflow validate [OPTIONS] [path]

  Valide un workflow. Sans `--remote`, la validation est locale et hors ligne.

Arguments:
  path  [default: .choregos/workflow.yaml]

Options:
  --remote  valider via l'API plutôt qu'en local
  --help    Show this message and exit.
```
