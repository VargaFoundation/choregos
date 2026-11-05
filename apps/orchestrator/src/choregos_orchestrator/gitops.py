# SPDX-License-Identifier: Apache-2.0
"""Rendering of a project's Kubernetes manifests, written to `choregos-infra/projects/<slug>/`.

Nobody applies a manifest by hand: the orchestrator writes to Git, Argo CD applies. That is what
makes provisioning auditable and reversible (`prune`).
"""

from __future__ import annotations

from typing import Any

import yaml
from choregos_contracts import Policy, ProjectConfig
from choregos_core.quotas import DEFAULT, Quota


def _dump(documents: list[dict[str, Any]]) -> str:
    return yaml.safe_dump_all(documents, sort_keys=False, allow_unicode=True)


#: A project's egress proxy: what a runner goes out through, and nothing else. Squid, run without
#: root, without capabilities, on a read-only filesystem, verified as is in a container
#: (allowlist: 200 on `api.github.com`, 403 elsewhere).
EGRESS_IMAGE = "docker.io/ubuntu/squid:6.6-24.04_beta"
EGRESS_PORT = 3128
EGRESS_NAME = "choregos-egress"
KUBE_NS = "kubernetes.io/metadata.name"
#: What the proxy NEVER reaches: the cluster itself and the link-local range (cloud metadata).
PRIVATE_RANGES = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"]


def squid_config(allowed_domains: list[str]) -> str:
    """The proxy's configuration: an allowlist by domain, everything else refused.

    `dstdomain .github.com` covers `github.com` and its subdomains. `CONNECT` is only accepted to
    443: a tunnel to another port would be a disguised exit. No cache (a runner has nothing to
    share with the next), no `Via` and no `X-Forwarded-For` (the provider has no need to know the
    pod's address), access log on standard output: that is where `kubectl logs` reads it.
    """
    domains = " ".join(sorted({f".{d.lstrip('.')}" for d in allowed_domains}))
    return "\n".join(
        [
            f"http_port {EGRESS_PORT}",
            f"acl allowed dstdomain {domains}",
            "acl SSL_ports port 443",
            "acl Safe_ports port 80 443",
            "acl CONNECT method CONNECT",
            "http_access deny !Safe_ports",
            "http_access deny CONNECT !SSL_ports",
            "http_access allow allowed",
            "http_access deny all",
            "cache deny all",
            # Without a cache, Squid still reserves 256 MB of `cache_mem` by default: under a
            # 256 Mi limit the pod was killed (OOM) before listening.
            "cache_mem 8 MB",
            "memory_pools off",
            # Squid sizes its tables on `ulimit -n`: under containerd, over a billion, so the pod was
            # killed (OOM) within the second, whatever its memory limit. Reproduced in a container
            # with that ulimit, cured by this line.
            "max_filedescriptors 4096",
            "access_log stdio:/dev/stdout",
            "cache_log /dev/stderr",
            "pid_filename none",
            "logfile_rotate 0",
            "pinger_enable off",
            "via off",
            "forwarded_for delete",
            "",
        ]
    )


def render_egress_proxy(
    namespace: str, slug: str, allowed_domains: list[str], image: str = EGRESS_IMAGE
) -> str:
    """The egress proxy of the runners' namespace: ConfigMap, Deployment, Service, and the
    NetworkPolicy that opens the Internet to IT ALONE. The namespace's `default-deny-egress` also
    applies to the proxy; this policy lifts it only for its pod, to the public web ports, never to
    the cluster's private ranges.
    """
    labels = {"app.kubernetes.io/name": EGRESS_NAME, "choregos/project": slug}
    # The only directories Squid writes; emptyDirs, the rest of the filesystem read-only.
    volumes = (("log", "/var/log/squid"), ("spool", "/var/spool/squid"), ("run", "/run"), ("tmp", "/tmp"))  # noqa: S108
    return _dump(
        [
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": EGRESS_NAME, "namespace": namespace, "labels": labels},
                "data": {"squid.conf": squid_config(allowed_domains)},
            },
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": EGRESS_NAME, "namespace": namespace, "labels": labels},
                "spec": {
                    "replicas": 1,
                    "selector": {"matchLabels": labels},
                    "template": {
                        "metadata": {"labels": labels},
                        "spec": {
                            "automountServiceAccountToken": False,
                            "securityContext": {
                                "runAsNonRoot": True,
                                # `proxy`, the image's user; Squid needs nothing else.
                                "runAsUser": 13,
                                "runAsGroup": 13,
                                "seccompProfile": {"type": "RuntimeDefault"},
                            },
                            "containers": [
                                {
                                    "name": "squid",
                                    "image": image,
                                    # Without the image's entrypoint: it wants to create a
                                    # certificate and cache directories, as root.
                                    "command": ["squid", "-N", "-f", "/etc/squid/choregos.conf"],
                                    "ports": [{"name": "proxy", "containerPort": EGRESS_PORT}],
                                    "readinessProbe": {"tcpSocket": {"port": "proxy"}, "periodSeconds": 5},
                                    "resources": {
                                        "requests": {"cpu": "50m", "memory": "64Mi"},
                                        "limits": {"cpu": "500m", "memory": "256Mi"},
                                    },
                                    "securityContext": {
                                        "allowPrivilegeEscalation": False,
                                        "readOnlyRootFilesystem": True,
                                        "capabilities": {"drop": ["ALL"]},
                                    },
                                    "volumeMounts": [
                                        {
                                            "name": "config",
                                            "mountPath": "/etc/squid/choregos.conf",
                                            "subPath": "squid.conf",
                                            "readOnly": True,
                                        },
                                        *({"name": name, "mountPath": path} for name, path in volumes),
                                    ],
                                }
                            ],
                            "volumes": [
                                {"name": "config", "configMap": {"name": EGRESS_NAME}},
                                *({"name": name, "emptyDir": {}} for name, _ in volumes),
                            ],
                        },
                    },
                },
            },
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": EGRESS_NAME, "namespace": namespace, "labels": labels},
                "spec": {
                    "selector": labels,
                    "ports": [{"name": "proxy", "port": EGRESS_PORT, "targetPort": "proxy"}],
                },
            },
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {"name": f"{EGRESS_NAME}-out", "namespace": namespace},
                "spec": {
                    "podSelector": {"matchLabels": {"app.kubernetes.io/name": EGRESS_NAME}},
                    "policyTypes": ["Egress"],
                    "egress": [
                        {
                            "to": [
                                {
                                    "ipBlock": {
                                        "cidr": "0.0.0.0/0",
                                        "except": PRIVATE_RANGES,
                                    }
                                }
                            ],
                            "ports": [{"protocol": "TCP", "port": 443}, {"protocol": "TCP", "port": 80}],
                        },
                        {
                            "to": [{"namespaceSelector": {"matchLabels": {KUBE_NS: "kube-system"}}}],
                            "ports": [{"protocol": "UDP", "port": 53}],
                        },
                    ],
                },
            },
        ]
    )


def render_project_manifests(
    slug: str,
    config: ProjectConfig,
    policy: Policy,
    *,
    egress_image: str = EGRESS_IMAGE,
    quota: Quota = DEFAULT,
    org_allow_domains: list[str] | None = None,
) -> dict[str, str]:
    """Render a project's files: namespaces, quotas, netpol, RBAC, egress, Tekton, Argo.

    `org_allow_domains`: the hosts the organisation opened for its runners (S39-22d), joined to the
    policy's own allowlist in the proxy and in the network policy's annotation.

    `quota` comes from `choregos_core.quotas`: an extension sets it per organisation; it used to be fixed.
    """
    runners = f"proj-{slug}-runners"
    ci = f"proj-{slug}-ci"
    quota_cpu = quota.cpu
    quota_memory = quota.memory
    gvisor = policy.sandbox.runtime == "gvisor"

    namespaces = _dump(
        [
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {
                    "name": ns,
                    "labels": {
                        "choregos/project": slug,
                        "pod-security.kubernetes.io/enforce": "restricted",
                        "pod-security.kubernetes.io/audit": "restricted",
                    },
                },
            }
            for ns in (runners, ci)
        ]
    )

    quotas = _dump(
        [
            {
                "apiVersion": "v1",
                "kind": "ResourceQuota",
                "metadata": {"name": "choregos-quota", "namespace": ns, "labels": {"choregos/project": slug}},
                "spec": {
                    "hard": {
                        "requests.cpu": quota_cpu,
                        "requests.memory": quota_memory,
                        "limits.cpu": quota_cpu,
                        "limits.memory": quota_memory,
                        "count/pods": str(quota.pods),
                    }
                },
            }
            for ns in (runners, ci)
        ]
        + [
            {
                "apiVersion": "v1",
                "kind": "LimitRange",
                "metadata": {"name": "choregos-limits", "namespace": runners},
                "spec": {
                    "limits": [
                        {
                            "type": "Container",
                            "default": {"cpu": "2", "memory": "6Gi"},
                            "defaultRequest": {"cpu": "1", "memory": "2Gi"},
                        }
                    ]
                },
            }
        ]
    )

    allowed_domains = sorted({*policy.sandbox.network.allow_domains, *(org_allow_domains or [])})
    # The Internet, for a runner, is the namespace's proxy, and only if there are domains to
    # allow. Before: the policy opened a `choregos-egress` namespace that no chart delivered; the
    # allowlist was only an annotation (state of play of 2026-09-24). With no domain, no proxy:
    # the door does not exist.
    proxy_egress = (
        [
            {
                "to": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": EGRESS_NAME}}}],
                "ports": [{"protocol": "TCP", "port": EGRESS_PORT}],
            }
        ]
        if allowed_domains
        else []
    )
    netpol = _dump(
        [
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {"name": "default-deny-egress", "namespace": runners},
                "spec": {"podSelector": {}, "policyTypes": ["Egress"]},
            },
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {
                    "name": "allow-platform-egress",
                    "namespace": runners,
                    "annotations": {"choregos/allow-domains": ",".join(allowed_domains)},
                },
                "spec": {
                    "podSelector": {},
                    "policyTypes": ["Egress"],
                    "egress": [
                        {
                            "to": [
                                {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": ns}}}
                            ],
                            "ports": [{"protocol": "TCP", "port": port}],
                        }
                        for ns, port in (
                            ("choregos-system", 8000),
                            ("choregos-gateway", 4000),
                            ("choregos-memory", 8432),
                            ("monitoring", 4318),
                        )
                    ]
                    + proxy_egress
                    + [
                        {
                            "to": [
                                {
                                    "namespaceSelector": {
                                        "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                                    }
                                }
                            ],
                            "ports": [{"protocol": "UDP", "port": 53}],
                        }
                    ],
                },
            },
        ]
    )

    rbac = _dump(
        [
            {
                "apiVersion": "v1",
                "kind": "ServiceAccount",
                "metadata": {"name": "choregos-runner", "namespace": runners},
                "automountServiceAccountToken": False,
            },
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "Role",
                "metadata": {"name": "choregos-executor", "namespace": runners},
                "rules": [
                    {
                        "apiGroups": ["tekton.dev"],
                        "resources": ["pipelineruns", "taskruns"],
                        "verbs": ["create", "get", "list", "watch", "delete"],
                    },
                    # The SAME verbs as the chart's Role (`rbac-jobs.yaml`): this Role is a real
                    # project namespace's, and it had neither `patch` on Jobs (waking a queued
                    # run) nor `update`/`patch` on Secrets (replacing, then re-attaching the
                    # token). The bench runs in the chart's namespace and never saw it; a
                    # provisioned project would have paid for it in runs frozen in the queue:
                    # the failed admission is silent, by choice.
                    {
                        "apiGroups": [""],
                        "resources": ["secrets", "configmaps"],
                        "verbs": ["create", "get", "update", "patch", "delete"],
                    },
                    {
                        "apiGroups": ["batch"],
                        "resources": ["jobs"],
                        "verbs": ["create", "get", "list", "watch", "patch", "delete"],
                    },
                    {"apiGroups": [""], "resources": ["pods", "pods/log"], "verbs": ["get", "list", "watch"]},
                ],
            },
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "RoleBinding",
                "metadata": {"name": "choregos-executor", "namespace": runners},
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "Role",
                    "name": "choregos-executor",
                },
                "subjects": [
                    {
                        "kind": "ServiceAccount",
                        "name": "choregos-orchestrator",
                        "namespace": "choregos-system",
                    }
                ],
            },
        ]
    )

    apps = config.gitops.apps if config.gitops else [slug]
    repo_url = config.gitops.repo_url if config.gitops else ""
    path_prefix = config.gitops.path_prefix if config.gitops else "apps"
    argocd = _dump(
        [
            {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "Application",
                "metadata": {
                    "name": f"{app}-{env}",
                    "namespace": "argocd",
                    "labels": {"choregos/project": slug, "choregos/env": env},
                    "finalizers": ["resources-finalizer.argocd.argoproj.io"],
                },
                "spec": {
                    "project": "default",
                    "source": {
                        "repoURL": repo_url,
                        "path": f"{path_prefix}/{app}/overlays/{env}",
                        "targetRevision": "HEAD",
                    },
                    "destination": {"server": "https://kubernetes.default.svc", "namespace": f"{slug}-{env}"},
                    "syncPolicy": {
                        "automated": {"prune": True, "selfHeal": env != "prod"},
                        "syncOptions": ["CreateNamespace=true"],
                    },
                },
            }
            for app in apps
            for env in config.envs
        ]
    )

    tekton = _dump(
        [
            {
                "apiVersion": "triggers.tekton.dev/v1beta1",
                "kind": "EventListener",
                "metadata": {"name": f"{slug}-github", "namespace": ci},
                "spec": {
                    "serviceAccountName": "tekton-triggers",
                    "triggers": [
                        {
                            "name": "pull-request",
                            "interceptors": [
                                {
                                    "ref": {"name": "github"},
                                    "params": [
                                        {
                                            "name": "secretRef",
                                            "value": {"secretName": f"{slug}-webhook", "secretKey": "secret"},
                                        },
                                        {"name": "eventTypes", "value": ["pull_request", "push"]},
                                    ],
                                }
                            ],
                            "bindings": [{"ref": f"{slug}-binding"}],
                            "template": {"ref": f"{slug}-ci-template"},
                        }
                    ],
                },
            }
        ]
    )

    kustomization = _dump(
        [
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "commonLabels": {"choregos/project": slug},
                "resources": [
                    "namespaces.yaml",
                    "quotas.yaml",
                    "netpol.yaml",
                    "rbac.yaml",
                    *(["egress.yaml"] if allowed_domains else []),
                    "tekton.yaml",
                    "argocd.yaml",
                    # Argo CD applies what this lists, nothing else of the directory (S41-17).
                    *(["runtimeclass.yaml"] if gvisor else []),
                ],
            }
        ]
    )

    files = {
        "namespaces.yaml": namespaces,
        "quotas.yaml": quotas,
        "netpol.yaml": netpol,
        "rbac.yaml": rbac,
        "argocd.yaml": argocd,
        "tekton.yaml": tekton,
        "kustomization.yaml": kustomization,
    }
    if allowed_domains:
        files["egress.yaml"] = render_egress_proxy(runners, slug, allowed_domains, image=egress_image)
    if gvisor:
        files["runtimeclass.yaml"] = _dump(
            [
                {
                    "apiVersion": "kyverno.io/v1",
                    "kind": "Policy",
                    "metadata": {"name": "require-gvisor", "namespace": runners},
                    "spec": {
                        "validationFailureAction": "Enforce",
                        "rules": [
                            {
                                "name": "runtimeclass-gvisor",
                                "match": {"any": [{"resources": {"kinds": ["Pod"]}}]},
                                "validate": {
                                    "message": "this project's runners must use gVisor",
                                    "pattern": {"spec": {"runtimeClassName": "gvisor"}},
                                },
                            }
                        ],
                    },
                }
            ]
        )
    return files
