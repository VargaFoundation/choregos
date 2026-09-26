"""Configuration de l'API (12-factor, préfixe `CHOREGOS_`)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Ce qu'un moteur de gabarit Go écrit quand la variable n'existe pas : il rend littéralement
#: cette chaîne au lieu d'échouer. Helm et l'opérateur Infisical le font tous les deux.
GABARIT_NON_RESOLU = ("<no value>", "<nil>")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHOREGOS_", env_file=".env", extra="ignore")

    env: Literal["dev", "staging", "prod", "test"] = "dev"
    # Port d'écoute. En conteneur, 8000 ; sur une machine de développement, 8000 est
    # souvent déjà pris, et `choregos-api` sortait sur « Address already in use » sans
    # qu'on puisse en changer autrement qu'en éditant le code.
    port: int = 8000
    debug: bool = False
    fakes: bool = Field(default=False, validation_alias="CHOREGOS_FAKES")

    # Base de données
    database_url: str = "sqlite+aiosqlite:///./choregos.db"
    db_echo: bool = False
    #: Pool de connexions, BORNÉ. `pool_size` seul laissait SQLAlchemy ouvrir dix
    #: connexions de débordement de plus, et attendre trente secondes une connexion
    #: libre (état des lieux du 2026-09-24). Chaque processus tient au plus
    #: `db_pool_size + db_max_overflow` connexions ; `db_pool_recycle_s` ferme celles
    #: qu'un pare-feu ou PgBouncer aurait coupées en silence.
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_pool_timeout_s: int = 10
    db_pool_recycle_s: int = 1800

    # Identité
    oidc_issuer: str = "http://localhost:8080/realms/choregos"
    oidc_client_id: str = "choregos"
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid profile email groups"
    session_secret: str = "dev-session-secret-change-me"
    session_cookie: str = "choregos_session"
    session_max_age_s: int = 8 * 3600
    #: Connexion de développement (`/auth/login?as=<email>`) : ÉTEINTE par défaut. Elle
    #: était allumée par défaut et jamais posée par le chart : une installation de
    #: production acceptait `?code=dev:admin@x` et rendait ORG_ADMIN sur toutes les
    #: organisations (état des lieux du 2026-09-24). Le validateur ci-dessous la refuse
    #: en staging et en prod quoi qu'on lui dise.
    dev_login_enabled: bool = False
    #: Les e-mails qui, en connexion de développement, reçoivent ORG_ADMIN. Les autres
    #: sont `developers`. Avant : tout e-mail commençant par `admin` — une escalade par
    #: convention de nommage.
    dev_admin_emails: str = ""
    #: Amorçage : l'organisation créée au premier démarrage si elle n'existe pas, et les
    #: e-mails qui y reçoivent ORG_ADMIN. Sans cela, une installation neuve n'avait AUCUN
    #: moyen de créer une organisation (seuls les scripts de seed le faisaient) — donc aucun
    #: projet, donc rien (état des lieux du 2026-09-24). Idempotent : rejoué à chaque
    #: démarrage, il ne touche pas à ce qui existe.
    bootstrap_org: str = ""
    bootstrap_org_name: str = ""
    bootstrap_admins: str = ""
    #: Organisation à laquelle rattacher les groupes OIDC NON préfixés (`developers`,
    #: `org-admins`…). Vide : ces groupes sont ignorés, seuls les groupes
    #: `choregos:<org>:<groupe>` donnent un rôle. Avant : tout groupe donnait le rôle sur
    #: TOUTES les organisations de l'instance.
    oidc_default_org: str = ""

    # JWT de run (ES256) — clés PEM ; générées à la volée en dev
    run_token_private_key: str = ""
    run_token_public_key: str = ""
    run_token_issuer: str = "choregos-api"
    run_token_audience: str = "internal"

    # Services
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "orchestrator"
    gateway_url: str = "http://localhost:4000"
    gateway_master_key: str = ""
    memory_url: str = "http://localhost:8432"
    object_store_url: str = "s3://choregos"
    public_url: str = "http://localhost:3000"
    api_url: str = "http://localhost:8000"

    # Webhooks
    github_webhook_secret: str = ""
    github_app_id: str = ""
    github_app_private_key: str = ""
    #: Secret partagé des webhooks non signés (ArgoCD, Alertmanager, Jira, GitLab). VIDE par
    #: défaut : `verify_shared_secret` refuse alors tout, et c'est voulu — la valeur par
    #: défaut d'avant était publiée dans ce dépôt, et le chart ne l'injectait pas.
    generic_webhook_secret: str = ""

    # Divers
    cors_origins: str = "http://localhost:3000"
    #: Requêtes par minute et par adresse sur les routes sans principal (`/auth/*`,
    #: `/webhooks/*`), par réplique. 0 désactive. Voir `limiteur.py`.
    rate_limit_per_minute: int = 600
    #: Période de recalcul des métriques Prometheus depuis la base (voir `metriques.py`).
    metrics_refresh_seconds: float = 15.0
    log_level: str = "INFO"
    log_json: bool = True
    fx_usd_eur: float = 0.92

    @model_validator(mode="after")
    def _pas_de_porte_derobee_en_production(self) -> Settings:
        if self.dev_login_enabled and self.env in {"staging", "prod"}:
            raise ValueError(
                "CHOREGOS_DEV_LOGIN_ENABLED=true en staging/prod : la connexion de développement "
                "ouvre l'API à quiconque atteint /auth/callback. Refusé."
            )
        return self

    @model_validator(mode="after")
    def _aucun_gabarit_non_resolu(self) -> Settings:
        """Un réglage qui vaut `<no value>` n'est pas une valeur : c'est un gabarit qui a raté.

        Le 2026-09-26, sur le locataire dev, `GATEWAY_MASTER_KEY` et `PLATFORM_LLM_KEY`
        contenaient tous deux la chaîne `<no value>` — dix caractères, l'air d'une valeur. Le
        pod LiteLLM était `1/1 Running`, le locataire paraissait sain, et la passerelle
        répondait 401 au premier appel de modèle : « LiteLLM Virtual Key expected.
        Received=<no value> ». Rien, nulle part, ne disait que la clé n'avait jamais été
        résolue.

        C'est un mode de panne de tout secret rendu par un gabarit Go — Helm comme l'opérateur
        Infisical — et il touche aussi bien le secret de session que les clés de jetons. On
        refuse de démarrer, dans TOUS les environnements : personne n'écrit `<no value>` exprès.
        """
        fautifs = sorted(
            nom
            for nom, valeur in self.__dict__.items()
            if isinstance(valeur, str) and valeur.strip() in GABARIT_NON_RESOLU
        )
        if fautifs:
            raise ValueError(
                f"réglages non résolus : {fautifs}. Leur valeur est littéralement « <no value> », "
                "ce qu'un gabarit Go écrit quand la variable n'existe pas — la clé n'a jamais été "
                "posée. Vérifier la source du secret (Infisical, Helm) avant de redémarrer."
            )
        return self

    @property
    def bootstrap_admin_emails(self) -> list[str]:
        return [e.strip().lower() for e in self.bootstrap_admins.split(",") if e.strip()]

    @property
    def dev_admins(self) -> set[str]:
        return {e.strip().lower() for e in self.dev_admin_emails.split(",") if e.strip()}

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
