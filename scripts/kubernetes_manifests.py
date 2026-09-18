"""Render reviewable Kubernetes JSON manifests for the single-node local deployment."""

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "relay"
BACKEND_IMAGE = "relay-workspace-backend:k8s-v4.2"
WEB_IMAGE = "relay-workspace-web:k8s-v4.1"


def resource(kind, name, api="v1", **fields):
    return {"apiVersion": api, "kind": kind, "metadata": {"name": name, "namespace": NAMESPACE}, **fields}


def secret_env(name, key, secret="relay-database"):
    return {"name": name, "valueFrom": {"secretKeyRef": {"name": secret, "key": key}}}


def container(name, image, command=None):
    value = {
        "name": name,
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
        "resources": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "1", "memory": "512Mi"},
        },
    }
    if command:
        value["command"] = command
    return value


def pod(containers, uid=10001, volumes=None):
    return {
        "automountServiceAccountToken": False,
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": uid,
            "runAsGroup": uid,
            "fsGroup": uid,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "terminationGracePeriodSeconds": 90,
        "containers": containers,
        "volumes": volumes or [],
    }


def service(name, component, port, target, node_port=None):
    port_spec = {"name": "service", "port": port, "targetPort": target}
    if node_port:
        port_spec["nodePort"] = node_port
    return resource(
        "Service",
        name,
        spec={
            "selector": {"app": "relay", "component": component},
            "ports": [port_spec],
            "type": "NodePort" if node_port else "ClusterIP",
        },
    )


def claim(name, size):
    return resource(
        "PersistentVolumeClaim",
        name,
        spec={"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": size}}},
    )


def workload(kind, name, spec):
    labels = {"app": "relay", "component": name}
    body = {
        "replicas": 1,
        "selector": {"matchLabels": labels},
        "template": {"metadata": {"labels": labels}, "spec": spec},
    }
    if kind == "StatefulSet":
        body["serviceName"] = name
    else:
        body["strategy"] = {"type": "Recreate"}
    return resource(kind, name, "apps/v1", spec=body)


def backend_container(name, command=None):
    value = container(name, BACKEND_IMAGE, command)
    value["envFrom"] = [
        {"configMapRef": {"name": "relay-config"}},
        {"secretRef": {"name": "relay-integrations"}},
    ]
    value["env"] = [secret_env("DATABASE_URL", "application-url")]
    value["volumeMounts"] = [{"name": "documents", "mountPath": "/data"}]
    return value


def manifests(live=True, bootstrap_email="admin@example.test"):
    if not live:
        raise ValueError("Demo deployment is retired; configure live inference explicitly")
    namespace = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": NAMESPACE, "labels": {"app.kubernetes.io/part-of": "relay-workspace"}},
    }
    config = resource(
        "ConfigMap",
        "relay-config",
        data={
            "APP_ENV": "development",
            "MODEL_PROVIDER": "openai",
            "SEARXNG_URL": "http://search:8080",
            "GMAIL_MCP_URL": "http://gmail-mcp:8101/mcp",
            "DRIVE_MCP_URL": "http://drive-mcp:8102/mcp",
            "CHAT_MODEL": "gpt-5.6-luna",
            "GRADER_MODEL": "gpt-5.6-terra",
            "EMBEDDING_PROVIDER": "local",
            "EMBEDDING_MODEL": "BAAI/bge-small-en-v1.5",
            "EMBEDDING_DIMENSIONS": "384",
            "EMBEDDING_MODEL_DIR": "/app/models/bge-small-en-v1.5",
            "ANSWER_REASONING_EFFORT": "low",
            "AGENT_REASONING_EFFORT": "medium",
            "GRADER_REASONING_EFFORT": "medium",
            "MODEL_MAX_OUTPUT_TOKENS": "4096",
            "PROVIDER_TIMEOUT_SECONDS": "45",
            "GRADER_CALIBRATION_FILE": "/data/grader-calibration.json",
            "APP_ORIGIN": "http://localhost:8088",
            "COOKIE_SECURE": "false",
            "JOB_MODE": "celery",
            "REDIS_URL": "redis://redis:6379/0",
            "DATA_DIR": "/data",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TRACE_RETENTION_DAYS": "30",
            "MAX_JOB_ATTEMPTS": "3",
            "BOOTSTRAP_EMAIL": bootstrap_email,
        },
    )
    init = resource(
        "ConfigMap",
        "relay-postgres-init",
        data={"10-relay-role.sh": (ROOT / "ops/postgres-init.sh").read_text().replace("\r\n", "\n")},
    )
    pg = container("postgres", "pgvector/pgvector:pg17")
    pg["env"] = [
        {"name": "POSTGRES_USER", "value": "relay"},
        {"name": "POSTGRES_DB", "value": "relay"},
        {"name": "PGDATA", "value": "/var/lib/postgresql/data/pgdata"},
        secret_env("POSTGRES_PASSWORD", "admin-password"),
        secret_env("POSTGRES_APP_PASSWORD", "application-password"),
    ]
    pg["ports"] = [{"containerPort": 5432, "name": "postgres"}]
    pg["volumeMounts"] = [
        {"name": "database", "mountPath": "/var/lib/postgresql/data"},
        {"name": "init", "mountPath": "/docker-entrypoint-initdb.d", "readOnly": True},
    ]
    pg["readinessProbe"] = {
        "exec": {"command": ["pg_isready", "-U", "relay", "-d", "relay"]},
        "periodSeconds": 5,
    }
    pg["startupProbe"] = {**copy.deepcopy(pg["readinessProbe"]), "failureThreshold": 60}
    pg["resources"]["limits"] = {"cpu": "2", "memory": "1Gi"}
    postgres = workload(
        "StatefulSet",
        "db",
        pod(
            [pg],
            999,
            [
                {"name": "database", "persistentVolumeClaim": {"claimName": "relay-postgres"}},
                {"name": "init", "configMap": {"name": "relay-postgres-init", "defaultMode": 365}},
            ],
        ),
    )
    redis = container("redis", "redis:7-alpine", ["redis-server", "--appendonly", "yes", "--dir", "/data"])
    redis["volumeMounts"] = [{"name": "redis", "mountPath": "/data"}]
    redis["readinessProbe"] = {"exec": {"command": ["redis-cli", "ping"]}, "periodSeconds": 5}
    redis["livenessProbe"] = {
        "exec": {"command": ["redis-cli", "ping"]},
        "periodSeconds": 15,
        "initialDelaySeconds": 15,
    }
    redis_set = workload(
        "StatefulSet",
        "redis",
        pod([redis], 999, [{"name": "redis", "persistentVolumeClaim": {"claimName": "relay-redis"}}]),
    )
    infrastructure = [
        namespace,
        config,
        init,
        claim("relay-postgres", "10Gi"),
        claim("relay-redis", "2Gi"),
        claim("relay-documents", "10Gi"),
        service("db", "db", 5432, 5432),
        service("redis", "redis", 6379, 6379),
        postgres,
        redis_set,
    ]

    volumes = [{"name": "documents", "persistentVolumeClaim": {"claimName": "relay-documents"}}]
    migration = backend_container("migrate", ["python", "-m", "backend.manage", "migrate"])
    migration["env"] = [secret_env("DATABASE_URL", "migration-url")]
    bootstrap = backend_container("bootstrap", ["python", "-m", "backend.manage", "bootstrap"])
    bootstrap["env"].append(secret_env("BOOTSTRAP_PASSWORD", "bootstrap-password", "relay-live-bootstrap"))
    setup_pod = pod([bootstrap], volumes=volumes)
    setup_pod.update({"restartPolicy": "Never", "initContainers": [migration]})
    setup = resource(
        "Job",
        "relay-setup-v4",
        "batch/v1",
        spec={
            "backoffLimit": 2,
            "activeDeadlineSeconds": 600,
            "template": {"metadata": {"labels": {"app": "relay", "component": "setup"}}, "spec": setup_pod},
        },
    )

    api = backend_container("api")
    api["ports"] = [{"name": "http", "containerPort": 8000}]
    api["startupProbe"] = {
        "httpGet": {"path": "/ready", "port": 8000},
        "periodSeconds": 5,
        "failureThreshold": 60,
        "timeoutSeconds": 5,
    }
    api["readinessProbe"] = {
        "httpGet": {"path": "/ready", "port": 8000},
        "periodSeconds": 10,
        "timeoutSeconds": 5,
    }
    api["livenessProbe"] = {"tcpSocket": {"port": 8000}, "periodSeconds": 15, "timeoutSeconds": 3}
    worker = backend_container(
        "worker",
        ["celery", "-A", "backend.jobs:celery_app", "worker", "--loglevel=warning", "--concurrency=5"],
    )
    worker["resources"] = {
        "requests": {"cpu": "250m", "memory": "512Mi"},
        "limits": {"cpu": "2", "memory": "2Gi"},
    }
    scheduler = backend_container(
        "scheduler",
        [
            "celery",
            "-A",
            "backend.jobs:celery_app",
            "beat",
            "--loglevel=warning",
            "--schedule=/data/celerybeat-schedule",
        ],
    )
    backend = workload("Deployment", "backend", pod([api, worker, scheduler], volumes=volumes))
    for item in [api, worker, scheduler, bootstrap]:
        item["env"].append(secret_env("OPENAI_API_KEY", "api-key", "relay-openai"))
    api["resources"]["limits"]["memory"] = "1Gi"
    worker["command"][-1] = "--concurrency=2"
    web = container("web", WEB_IMAGE)
    web["ports"] = [{"containerPort": 8080, "name": "http"}]
    web["readinessProbe"] = {
        "httpGet": {"path": "/ready", "port": 8080},
        "periodSeconds": 5,
        "timeoutSeconds": 5,
    }
    web["livenessProbe"] = {"tcpSocket": {"port": 8080}, "periodSeconds": 15}
    frontend = workload("Deployment", "web", pod([web], 101))
    workloads = [
        service("api", "backend", 8000, 8000),
        service("web", "web", 8080, 8080, 30080),
        backend,
        frontend,
    ]
    infrastructure.append(
        resource(
            "ConfigMap",
            "relay-search-config",
            data={"settings.yml": (ROOT / "ops/searxng-settings.yml").read_text()},
        )
    )
    for name, port in [("gmail", 8101), ("drive", 8102)]:
        item = backend_container(name + "-mcp", ["python", "-m", "backend.mcp_servers", name])
        item.pop("volumeMounts")
        item["ports"] = [{"containerPort": port}]
        item["readinessProbe"] = {"httpGet": {"path": "/health", "port": port}, "periodSeconds": 10}
        item["livenessProbe"] = {"httpGet": {"path": "/health", "port": port}, "periodSeconds": 20}
        workloads.extend(
            [
                service(name + "-mcp", name + "-mcp", port, port),
                workload("Deployment", name + "-mcp", pod([item])),
            ]
        )
    search = container(
        "search", "searxng/searxng@sha256:d0a4ca04e68c6d57fe45509ad5a6b10c890350724762ade3abea5363571aa29a"
    )
    search["env"] = [secret_env("SEARXNG_SECRET", "SEARXNG_SECRET", "relay-integrations")]
    search["ports"] = [{"containerPort": 8080}]
    search["volumeMounts"] = [
        {
            "name": "settings",
            "mountPath": "/etc/searxng/settings.yml",
            "subPath": "settings.yml",
            "readOnly": True,
        },
        {"name": "cache", "mountPath": "/var/cache/searxng"},
    ]
    search["readinessProbe"] = {
        "httpGet": {"path": "/healthz", "port": 8080},
        "initialDelaySeconds": 15,
        "periodSeconds": 10,
    }
    search["livenessProbe"] = {
        "httpGet": {"path": "/healthz", "port": 8080},
        "initialDelaySeconds": 30,
        "periodSeconds": 20,
    }
    workloads.extend(
        [
            service("search", "search", 8080, 8080),
            workload(
                "Deployment",
                "search",
                pod(
                    [search],
                    uid=977,
                    volumes=[
                        {"name": "settings", "configMap": {"name": "relay-search-config"}},
                        {"name": "cache", "emptyDir": {}},
                    ],
                ),
            ),
        ]
    )
    workloads.append(
        resource(
            "NetworkPolicy",
            "private-tool-services",
            "networking.k8s.io/v1",
            spec={
                "podSelector": {
                    "matchExpressions": [
                        {"key": "component", "operator": "In", "values": ["gmail-mcp", "drive-mcp", "search"]}
                    ]
                },
                "policyTypes": ["Ingress"],
                "ingress": [{"from": [{"podSelector": {"matchLabels": {"component": "backend"}}}]}],
            },
        )
    )
    return {"infrastructure": infrastructure, "setup": [setup], "workloads": workloads}


def main(live=True, bootstrap_email="admin@example.test"):
    folder = ROOT / "ops/kubernetes"
    folder.mkdir(parents=True, exist_ok=True)
    for name, items in manifests(live, bootstrap_email).items():
        (folder / f"{name}.json").write_text(
            json.dumps({"apiVersion": "v1", "kind": "List", "items": items}, indent=2) + "\n",
            encoding="utf-8",
        )
    print("Rendered Kubernetes manifests; no credentials are included.")
    return folder


if __name__ == "__main__":
    main()
