# 0073 — GitHub App installations are discovered, not typed

- **Status**: accepted, 2026-10-16
- **Concerns**: the GitHub connector types (`tracker/github-issues`, `scm/github`), the API
  (`services/github_app.py`, `routers/github_app.py`, `routers/connectors.py`, `routers/internal.py`,
  `routers/webhooks.py`), the orchestrator's `load_project`, the adapters (`github/auth.py`,
  `github/app.py`), the contract (`openapi.yaml`), the chart, the console (S40-04c); amends
  [ADR 0034](0034-connectors-by-capability.md) for the GitHub types; refers to
  [ADR 0058](0058-connector-secrets-are-write-only-values.md) (no secret in config)

## Context

The project settings asked for a GitHub **installation id**. Both GitHub connector types declared
`installation_id: integer` in their configuration schema, and the console draws every schema
property as a form field. The field was dead:

- **No factory read it.** `_github_client(cfg)` builds `GitHubAppAuth(app_id, private_key)` from the
  deployment's environment, and `GitHubAppAuth.token_for()` already discovers the installation with
  `GET /repos/{owner}/{repo}/installation`. The dev API log shows it: in four hours, eight
  `GET https://api.github.com/repos/DiametralGroup/choregos-sandbox-dev/installation`, each followed by
  `POST /app/installations/169371754/access_tokens`. The stored `installation_id: 169371754` was never
  read; only the trial script wrote it.
- **Nothing helped to install the App**: the provisioning step only tested the tracker and said
  "install the App, then retry"; the repository was free text, listing nothing.
- **Tenant isolation.** One App serves the deployment. Discovery by repository means any Choregos
  organisation that types `othercorp/private-repo` gets installation tokens for it as soon as
  `othercorp` installed the App for its own Choregos organisation. The runner's git token had the
  same flaw. Latent in the community edition (a single organisation, ADR 0024), real with several.
- GitHub cannot reach the dev host (VPN only): discovery cannot depend on webhooks.

## Decision

1. **Nobody types an installation id.** It leaves both schemas. The `repo` property gains
   `format: github-repo`: the console draws a picker over the repositories the App sees, with a
   "type it instead" escape for hosts the API cannot list.
2. **The platform holds installations.** Table `github_installations` (primary key = GitHub's
   installation id; account login/type, repository selection, suspension, the **claiming
   organisation**, who and when), under forced row-level security: a claimed row is readable by its
   organisation, an unclaimed one by the platform scope only. Filled by three paths: polling
   `GET /app/installations` (on *Refresh*, and lazily when older than ten minutes on a read), the
   **Setup URL** redirect after an install, and the `installation` / `installation_repositories`
   webhooks when GitHub can reach the deployment.
3. **Install from the console.** `POST /orgs/{org}/github/install-link` answers
   `https://github.com/apps/<slug>/installations/new?state=<signed>`. The slug is read from `GET /app`,
   never configured. The App's **Setup URL** is `<public url>/api/v1/github/setup`: a browser redirect,
   so it works over a VPN. The `state` is HMAC-signed with the session secret, lives 15 minutes and
   names the organisation, project, person and return path (a path of the console, never a URL).
4. **Claiming is the isolation boundary.** A token for `owner/repo` is minted only through an
   installation claimed by the project's organisation.
   - *One organisation on the instance* (community edition): the organisation claims every
     installation of the deployment's App implicitly, at sync time. No gesture.
   - *Several organisations*: the claim needs proof that the person manages the installation. The App
     asks for user authorization during installation, GitHub sends back a `code`, the API exchanges it
     with the App's OAuth client and checks that `GET /user/installations` contains the id. GitHub's
     documentation warns that the callback's `installation_id` can be forged, so it is never trusted
     alone there. Without the client secret configured the claim is refused (403) with a message naming
     `CHOREGOS_GITHUB_APP_CLIENT_SECRET`. An installation another organisation claimed is never
     re-claimed (409).
   - *Enforcement*: `PUT /projects/{id}/connectors/{kind}` for the GitHub types answers 422 with the
     install link when the owner has no installation claimed by the organisation (a deployment with no
     App, or a connector on another host through `api_url`, is left alone); the orchestrator's
     `load_project` and the connector test inject the claimed installation id into the adapter
     configuration (never stored, never shown), and `post_git_token` pins it. `GitHubAppAuth` takes a
     `pinned_installation` and `pinned_account`, and refuses a repository of another owner. With
     several organisations, a GitHub connector or a run on an unclaimed owner fails, naming the way
     out, instead of falling back to discovery.
5. **One App, many companies.** The deployment's App is made public ("Any account" in its GitHub
   settings). Each company installs it on its own GitHub organisation, selects its repositories, and
   its Choregos organisation claims it. A legacy `installation_id` in a request is dropped silently.

Rejected:

- Keeping `installation_id` "advanced" or hidden: the value is derivable, and a typed one can name
  someone else's installation.
- Webhook-only discovery: dev is unreachable by GitHub.
- Trusting the setup callback's `installation_id` without OAuth in a multi-organisation instance
  (forgeable). With one organisation there is nobody to steal from.
- One App per Choregos organisation now (needs secrets per organisation and the GitHub App Manifest
  flow). Deferred: `github_installations.app_id` holds the column for that day.

## Consequences

- The form never shows an installation id; the repository is chosen from what the App sees.
- A Choregos administrator of a second organisation cannot obtain tokens for a first organisation's
  repositories by typing their names. Existing connectors keep working: in a single-organisation
  instance, discovery is the fallback and the first sync claims the installations.
- New optional deployment settings: `CHOREGOS_GITHUB_APP_CLIENT_ID`, `CHOREGOS_GITHUB_APP_CLIENT_SECRET`
  (chart keys `github-app-client-id`, `github-app-client-secret`, optional).
- Gestures on github.com, not reachable from code: the Setup URL, "Redirect on update", "Any account"
  for other companies, the installation events, and the OAuth option plus client secret before a
  second organisation exists ([deployment](../deployment.md#the-github-app)).
- Does not cover: GitHub Enterprise Server hosts (`api_url`), which keep typing the repository and
  skip the claim; a per-organisation App.
