"""Rendu des manifests Kubernetes d'un projet, écrits dans `choregos-infra/projects/<slug>/`.

Personne n'applique de manifeste à la main : l'orchestrateur écrit dans Git, Argo CD applique.
C'est ce qui rend le provisioning auditable et réversible (`prune`).
"""

from __future__ import annotations

from typing import Any

import yaml
from choregos_contracts import Policy, ProjectConfig


def _dump(documents: list[dict[str, Any]]) -> str:
    return yaml.safe_dump_all(documents, sort_keys=False, allow_unicode=True)


def render_project_manifests(slug: str, config: ProjectConfig, policy: Policy) -> dict[str, str]:
    """Rend les fichiers d'un projet : namespaces, quotas, netpol, RBAC, Tekton, Argo."""
    runners = f"proj-{slug}-runners"
    ci = f"proj-{slug}-ci"
    quota_cpu = "32"
    quota_memory = "96Gi"
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
                        "count/pods": "40",
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

    allowed_domains = policy.sandbox.network.allow_domains
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
                            ("choregos-egress", 3128),
                            ("monitoring", 4318),
                        )
                    ]
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
                    {
                        "apiGroups": [""],
                        "resources": ["secrets", "configmaps"],
                        "verbs": ["create", "get", "delete"],
                    },
                    {
                        "apiGroups": ["batch"],
                        "resources": ["jobs"],
                        "verbs": ["create", "get", "list", "watch", "delete"],
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
                    "tekton.yaml",
                    "argocd.yaml",
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
                                    "message": "les runners de ce projet doivent utiliser gVisor",
                                    "pattern": {"spec": {"runtimeClassName": "gvisor"}},
                                },
                            }
                        ],
                    },
                }
            ]
        )
    return files
