# SPDX-License-Identifier: Apache-2.0
"""The netops connectors (ADR 0052): pfSense, Cilium and Hubble, nginx — our own estate first.

Each one READS through the platform (the broker of ADR 0034); none of them applies a change. A
change is a governed action (ADR 0035) or a pull request to the GitOps repository that the release
train carries to production (ADR 0006, ADR 0009).
"""

from __future__ import annotations

from .cilium import CILIUM_READ_OPERATIONS, CiliumNetwork, ReadOnlyKubernetes
from .hubble import FlowFilter, HubbleCli, RecordedFlows, normalize_flow, parse_jsonpb
from .nginx import NGINX_READ_OPERATIONS, GitHubRepoReader, NginxConfig
from .pfsense import PFSENSE_READ_OPERATIONS, PfSenseFirewall
from .tls import tls_verification

__all__ = [
    "CILIUM_READ_OPERATIONS",
    "NGINX_READ_OPERATIONS",
    "PFSENSE_READ_OPERATIONS",
    "CiliumNetwork",
    "FlowFilter",
    "GitHubRepoReader",
    "HubbleCli",
    "NginxConfig",
    "PfSenseFirewall",
    "ReadOnlyKubernetes",
    "RecordedFlows",
    "normalize_flow",
    "parse_jsonpb",
    "tls_verification",
]
