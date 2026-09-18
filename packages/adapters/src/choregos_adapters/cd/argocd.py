"""CdAdapter Argo CD + Argo Rollouts : promotion par PR GitOps, santé, canary, abandon.

Choregos ne fait **jamais** `kubectl apply` : il écrit dans le dépôt GitOps et Argo applique.
C'est ce qui rend une promotion auditable et réversible.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
from choregos_core.domain import Change, Health, PromotionRef, RolloutState, Window

from ..errors import ConfigurationError, UpstreamError
from ..github.client import GitHubClient


class ArgoCdAdapter:
    """API Argo CD pour la lecture, dépôt GitOps (via l'App GitHub) pour l'écriture."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str = "",
        gitops_repo: str = "",
        github: GitHubClient | None = None,
        app_pattern: str = "{app}-{env}",
        path_prefix: str = "apps",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.gitops_repo = gitops_repo
        self.github = github
        self.app_pattern = app_pattern
        self.path_prefix = path_prefix
        self._client = client or httpx.AsyncClient(timeout=30.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    def app_name(self, app: str, env: str = "prod") -> str:
        return self.app_pattern.format(app=app, env=env)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        response = await self._client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise UpstreamError(
                "argocd",
                f"{method} {path} → {response.status_code} : {response.text[:300]}",
                status_code=response.status_code,
            )
        return response.json() if response.content else {}

    async def current_revision(self, app: str) -> str:
        payload = await self._request("GET", f"/api/v1/applications/{app}")
        if payload is None:
            return "unknown"
        return str(((payload.get("status") or {}).get("sync") or {}).get("revision", "unknown"))

    async def health(self, app: str) -> Health:
        payload = await self._request("GET", f"/api/v1/applications/{app}")
        if payload is None:
            return Health(status="Missing", message=f"application {app} inconnue d'Argo CD")
        status = payload.get("status", {}) or {}
        health = status.get("health", {}) or {}
        return Health(
            status=str(health.get("status", "Unknown")),
            message=str(health.get("message", "")),
            revision=str((status.get("sync") or {}).get("revision", "")),
        )

    async def rollout_status(self, app: str) -> RolloutState:
        """État du Rollout : l'analyse SLO échouée se voit ici avant le rollback."""
        payload = await self._request(
            "GET",
            f"/api/v1/applications/{app}/resource",
            params={
                "name": app,
                "kind": "Rollout",
                "group": "argoproj.io",
                "version": "v1alpha1",
                "namespace": "",
            },
        )
        if payload is None:
            return RolloutState(phase="Unknown", message="aucun Rollout trouvé")
        import json

        manifest = payload.get("manifest")
        data = json.loads(manifest) if isinstance(manifest, str) else (manifest or {})
        status = data.get("status", {}) or {}
        current_step = int(status.get("currentStepIndex", 0))
        steps = len(((data.get("spec") or {}).get("strategy", {}).get("canary", {}) or {}).get("steps", []))
        phase = str(status.get("phase", "Unknown"))
        if any(
            condition.get("reason") == "RolloutAborted" for condition in status.get("conditions", []) or []
        ):
            phase = "Aborted"
        return RolloutState(
            phase=phase,
            current_step=current_step,
            total_steps=steps,
            canary_weight=int((status.get("canary", {}) or {}).get("weight", 0)),
            message=str(status.get("message", "")),
        )

    async def abort_rollout(self, app: str) -> None:
        await self._request(
            "POST",
            f"/api/v1/applications/{app}/rollback",
            json={"name": app, "prune": False},
        )

    async def set_sync_window(self, app: str, windows: list[Window]) -> None:
        """Garde-fou déclaratif : même si Choregos tombe, Argo refuse hors fenêtre."""
        payload = {
            "spec": {
                "syncWindows": [
                    {
                        "kind": window.kind,
                        "schedule": window.schedule,
                        "duration": window.duration,
                        "applications": window.applications or [app],
                        "timeZone": window.timezone,
                        "manualSync": True,
                    }
                    for window in windows
                ]
            }
        }
        await self._request("PATCH", f"/api/v1/applications/{app}", json=payload)

    async def promote(self, env: str, changes: list[Change], release: str) -> PromotionRef:
        """Écrit les tags d'images dans le dépôt GitOps, via une PR titrée `release(<env>)`."""
        if self.github is None or not self.gitops_repo:
            raise ConfigurationError(
                "promotion GitOps impossible : dépôt GitOps ou App GitHub non configurés"
            )
        branch = f"choregos/release-{release}".replace(" ", "-")
        base = await self._default_branch()
        await self._ensure_branch(branch, base)
        for change in changes:
            path = f"{self.path_prefix}/{change.app}/overlays/{env}/kustomization.yaml"
            await self._patch_image(path, branch, change)
        await self._write_manifest(env, release, changes, branch)
        body = _promotion_body(env, release, changes)
        pr = await self.github.request(
            "POST",
            f"/repos/{self.gitops_repo}/pulls",
            repo=self.gitops_repo,
            json={"title": f"release({env}): {release}", "head": branch, "base": base, "body": body},
        )
        return PromotionRef(kind="pr", url=pr.get("html_url"), ref=release, merged=False)

    async def _default_branch(self) -> str:
        assert self.github is not None
        repo = await self.github.request("GET", f"/repos/{self.gitops_repo}", repo=self.gitops_repo)
        return str(repo.get("default_branch", "main"))

    async def _ensure_branch(self, branch: str, base: str) -> None:
        assert self.github is not None
        try:
            await self.github.request(
                "GET", f"/repos/{self.gitops_repo}/git/ref/heads/{branch}", repo=self.gitops_repo
            )
            return
        except UpstreamError as exc:
            if exc.status_code != 404:
                raise
        head = await self.github.request(
            "GET", f"/repos/{self.gitops_repo}/git/ref/heads/{base}", repo=self.gitops_repo
        )
        await self.github.request(
            "POST",
            f"/repos/{self.gitops_repo}/git/refs",
            repo=self.gitops_repo,
            json={"ref": f"refs/heads/{branch}", "sha": head["object"]["sha"]},
        )

    async def _patch_image(self, path: str, branch: str, change: Change) -> None:
        assert self.github is not None
        current = await self.github.request(
            "GET",
            f"/repos/{self.gitops_repo}/contents/{path}",
            repo=self.gitops_repo,
            params={"ref": branch},
        )
        if current is None:
            return
        content = base64.b64decode(current["content"]).decode("utf-8")
        updated = _replace_image_tag(content, change)
        if updated == content:
            return
        await self.github.request(
            "PUT",
            f"/repos/{self.gitops_repo}/contents/{path}",
            repo=self.gitops_repo,
            json={
                "message": f"release: {change.app} → {change.tag}",
                "content": base64.b64encode(updated.encode()).decode(),
                "sha": current["sha"],
                "branch": branch,
            },
        )

    async def _write_manifest(self, env: str, release: str, changes: list[Change], branch: str) -> None:
        """`releases/<env>/manifest.yaml` : le journal du lot, lisible par un humain."""
        assert self.github is not None
        import yaml

        path = f"releases/{env}/manifest.yaml"
        payload = yaml.safe_dump(
            {
                "release": release,
                "env": env,
                "apps": [{"app": c.app, "tag": c.tag, "image": c.image} for c in changes],
            },
            sort_keys=False,
            allow_unicode=True,
        )
        current = await self.github.request(
            "GET", f"/repos/{self.gitops_repo}/contents/{path}", repo=self.gitops_repo, params={"ref": branch}
        )
        body: dict[str, Any] = {
            "message": f"release({env}): {release}",
            "content": base64.b64encode(payload.encode()).decode(),
            "branch": branch,
        }
        if current is not None:
            body["sha"] = current["sha"]
        await self.github.request(
            "PUT", f"/repos/{self.gitops_repo}/contents/{path}", repo=self.gitops_repo, json=body
        )

    async def test(self) -> dict[str, Any]:
        version = await self._request("GET", "/api/version")
        return {"ok": version is not None, "version": (version or {}).get("Version")}


def _replace_image_tag(content: str, change: Change) -> str:
    """Remplace `newTag:` dans le kustomization de l'overlay, sans toucher au reste."""
    if not change.tag:
        return content
    lines = content.splitlines()
    out: list[str] = []
    in_images = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("images:"):
            in_images = True
        elif in_images and stripped.startswith("newTag:"):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f"{indent}newTag: {change.tag}")
            continue
        elif in_images and stripped and not line.startswith((" ", "-")):
            in_images = False
        out.append(line)
    return "\n".join(out) + ("\n" if content.endswith("\n") else "")


def _promotion_body(env: str, release: str, changes: list[Change]) -> str:
    lines = [
        f"Promotion du lot **{release}** vers `{env}`.",
        "",
        "| application | image |",
        "|:--|:--|",
        *[f"| {change.app} | `{change.tag or change.image}` |" for change in changes],
        "",
        "_PR ouverte par le release train de Choregos. Le lot, les tickets et les risques",
        "sont détaillés dans `releases/{env}/manifest.yaml`._".replace("{env}", env),
    ]
    return "\n".join(lines)
