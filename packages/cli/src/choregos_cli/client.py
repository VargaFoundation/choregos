"""Client HTTP de la CLI : jeton d'API ou cookie de session, erreurs lisibles."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

CONFIG_PATH = Path(os.environ.get("CHOREGOS_CONFIG", Path.home() / ".config" / "choregos" / "config.json"))


@dataclass
class Profile:
    """Profil de connexion, stocké dans `~/.config/choregos/config.json`."""

    api_url: str = "http://localhost:8000"
    token: str = ""
    org: str = "varga"

    @classmethod
    def load(cls) -> Profile:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in {"api_url", "token", "org"}})
        return cls(
            api_url=os.environ.get("CHOREGOS_API_URL", "http://localhost:8000"),
            token=os.environ.get("CHOREGOS_TOKEN", ""),
            org=os.environ.get("CHOREGOS_ORG", "varga"),
        )

    def save(self) -> Path:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps({"api_url": self.api_url, "token": self.token, "org": self.org}, indent=2),
            encoding="utf-8",
        )
        CONFIG_PATH.chmod(0o600)
        return CONFIG_PATH


class ApiError(RuntimeError):
    def __init__(self, status_code: int, problem: dict[str, Any] | str) -> None:
        if isinstance(problem, dict):
            detail = problem.get("detail") or problem.get("title") or str(problem)
            errors = problem.get("errors") or []
            if errors:
                detail += "\n" + "\n".join(
                    f"  - {'.'.join(str(p) for p in e.get('loc', []))} : {e.get('msg', '')}" for e in errors
                )
        else:
            detail = str(problem)[:500]
        super().__init__(f"{status_code} — {detail}")
        self.status_code = status_code


class Client:
    """Appels à l'API Choregos. Toutes les commandes de la CLI passent par ici."""

    def __init__(self, profile: Profile | None = None) -> None:
        self.profile = profile or Profile.load()
        headers = {"Accept": "application/json"}
        if self.profile.token:
            headers["Authorization"] = f"Bearer {self.profile.token}"
        self._client = httpx.Client(
            base_url=self.profile.api_url.rstrip("/") + "/api/v1", headers=headers, timeout=30.0
        )

    def close(self) -> None:
        self._client.close()

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            try:
                raise ApiError(response.status_code, response.json())
            except (json.JSONDecodeError, ValueError):
                raise ApiError(response.status_code, response.text) from None
        return response.json() if response.content else None

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self.request("PUT", path, **kwargs)

    def stream_sse(self, path: str, **kwargs: Any) -> Any:
        """Suit un flux SSE (journal d'un run, provisioning)."""
        with self._client.stream(
            "GET", path, headers={"Accept": "text/event-stream"}, timeout=None, **kwargs
        ) as response:
            for line in response.iter_lines():
                if line.startswith("data:"):
                    payload = line.removeprefix("data:").strip()
                    if payload:
                        yield json.loads(payload)
