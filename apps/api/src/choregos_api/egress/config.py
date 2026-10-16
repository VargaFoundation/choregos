# SPDX-License-Identifier: Apache-2.0
"""The proxy's settings (`CHOREGOS_EGRESS_*`)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EgressSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHOREGOS_EGRESS_", env_file=".env", extra="ignore")

    #: The API's internal base URL, as the pod reaches it.
    api_url: str = "http://choregos-api:8000/api/v1"
    port: int = 3128
    health_port: int = 8081
    #: The destination ports a tunnel may open. Anything else is refused whatever the host.
    ports: list[int] = Field(default_factory=lambda: [443, 80])
    #: Hosts refused to every run of this deployment (instance deny), on top of what the API says.
    deny_domains: list[str] = Field(default_factory=list)
    #: How long a run's network is kept before the API is asked again: a revoked agent or a changed
    #: ceiling applies within this time.
    cache_seconds: float = Field(default=60, ge=0)
    max_tunnels_per_run: int = Field(default=64, ge=1)
    idle_timeout_s: float = Field(default=300, gt=0)
    #: How often the aggregated decisions are posted to the API.
    flush_seconds: float = Field(default=15, gt=0)
    #: Seconds a client has to send its request head.
    head_timeout_s: float = Field(default=30, gt=0)
    connect_timeout_s: float = Field(default=10, gt=0)
