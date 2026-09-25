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


#: Le proxy d'egress d'un projet : ce par quoi un runner sort, et rien d'autre. Squid,
#: exécuté sans root, sans capacité, sur un système de fichiers en lecture seule — vérifié
#: tel quel en conteneur (allowlist : 200 sur `api.github.com`, 403 ailleurs).
EGRESS_IMAGE = "docker.io/ubuntu/squid:6.6-24.04_beta"
EGRESS_PORT = 3128
EGRESS_NAME = "choregos-egress"
KUBE_NS = "kubernetes.io/metadata.name"
#: Ce que le proxy ne joint JAMAIS : le cluster lui-même et le lien local (métadonnées cloud).
PLAGES_PRIVEES = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"]


def squid_config(allowed_domains: list[str]) -> str:
    """La configuration du proxy : une allowlist par domaine, tout le reste refusé.

    `dstdomain .github.com` couvre `github.com` et ses sous-domaines. `CONNECT` n'est
    accepté que vers 443 : un tunnel vers un autre port serait une sortie déguisée. Pas de
    cache (un runner n'a rien à partager avec le suivant), pas de `Via` ni de
    `X-Forwarded-For` (le fournisseur n'a pas à connaître l'adresse du pod), journal
    d'accès sur la sortie standard : c'est là que `kubectl logs` le lit.
    """
    domaines = " ".join(sorted({f".{d.lstrip('.')}" for d in allowed_domains}))
    return "\n".join(
        [
            f"http_port {EGRESS_PORT}",
            f"acl allowed dstdomain {domaines}",
            "acl SSL_ports port 443",
            "acl Safe_ports port 80 443",
            "acl CONNECT method CONNECT",
            "http_access deny !Safe_ports",
            "http_access deny CONNECT !SSL_ports",
            "http_access allow allowed",
            "http_access deny all",
            "cache deny all",
            # Sans cache, Squid réserve quand même 256 Mo de `cache_mem` par défaut : sous
            # une limite de 256 Mi le pod était tué (OOM) avant d'écouter.
            "cache_mem 8 MB",
            "memory_pools off",
            # Squid dimensionne ses tables sur `ulimit -n` : sous containerd, plus d'un
            # milliard — le pod était tué (OOM) dans la seconde, quelle que soit sa limite
            # mémoire. Reproduit en conteneur avec ce ulimit, guéri par cette ligne.
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
    """Le proxy d'egress du namespace des runners : ConfigMap, Deployment, Service, et la
    NetworkPolicy qui n'ouvre Internet QU'À LUI. Le `default-deny-egress` du namespace
    s'applique aussi au proxy ; cette politique ne le lève que pour son pod, vers les ports
    web publics, jamais vers les plages privées du cluster.
    """
    labels = {"app.kubernetes.io/name": EGRESS_NAME, "choregos/project": slug}
    # Les seuls répertoires que Squid écrit ; des emptyDir, le reste du système en lecture seule.
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
                                # `proxy`, l'utilisateur de l'image ; Squid n'a besoin de rien d'autre.
                                "runAsUser": 13,
                                "runAsGroup": 13,
                                "seccompProfile": {"type": "RuntimeDefault"},
                            },
                            "containers": [
                                {
                                    "name": "squid",
                                    "image": image,
                                    # Sans le point d'entrée de l'image : il veut créer un
                                    # certificat et des répertoires de cache, en root.
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
                                        *({"name": nom, "mountPath": chemin} for nom, chemin in volumes),
                                    ],
                                }
                            ],
                            "volumes": [
                                {"name": "config", "configMap": {"name": EGRESS_NAME}},
                                *({"name": nom, "emptyDir": {}} for nom, _ in volumes),
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
                                        "except": PLAGES_PRIVEES,
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
    slug: str, config: ProjectConfig, policy: Policy, *, egress_image: str = EGRESS_IMAGE
) -> dict[str, str]:
    """Rend les fichiers d'un projet : namespaces, quotas, netpol, RBAC, egress, Tekton, Argo."""
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

    allowed_domains = list(policy.sandbox.network.allow_domains)
    # Internet, pour un runner, c'est le proxy du namespace — et seulement s'il y a des
    # domaines à autoriser. Avant : la politique ouvrait un namespace `choregos-egress`
    # qu'aucun chart ne livrait ; l'allowlist n'était qu'une annotation (état des lieux du
    # 2026-09-24). Sans domaine, pas de proxy : la porte n'existe pas.
    sortie_proxy = (
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
                    + sortie_proxy
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
                    # Les MÊMES verbes que le Role du chart (`rbac-jobs.yaml`) : ce Role-ci est
                    # celui d'un vrai namespace de projet, et il n'avait ni `patch` sur les
                    # Jobs (réveiller un run en file) ni `update`/`patch` sur les Secrets
                    # (remplacer, puis rattacher le jeton). Le banc tourne dans le namespace
                    # du chart et ne l'a jamais vu ; un projet provisionné l'aurait payé en
                    # runs figés en file — l'admission ratée se tait, par choix.
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
