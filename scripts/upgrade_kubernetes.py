"""Backed-up v4 upgrade. Preserve chat configuration, accounts and historical evidence."""

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from urllib.request import urlopen

from scripts.deploy_kubernetes import KUBECONFIG, kubectl, run, ensure_integration_secrets
from scripts.kubernetes_manifests import BACKEND_IMAGE, ROOT, WEB_IMAGE, manifests, resource


def migration_job():
    source = manifests()["setup"][0]["spec"]["template"]["spec"]
    source["containers"] = source.pop("initContainers")
    return resource(
        "Job",
        "relay-upgrade-v4",
        "batch/v1",
        spec={"backoffLimit": 2, "activeDeadlineSeconds": 600, "template": {"spec": source}},
    )


def upgrade(skip_build=False):
    config = json.loads(kubectl("get", "configmap", "relay-config", "-o", "json", capture=True).stdout)
    if config.get("data", {}).get("MODEL_PROVIDER") != "openai":
        raise RuntimeError(
            "Retired demo configuration detected. Configure OpenAI credentials and explicitly set MODEL_PROVIDER=openai before upgrading. Existing jobs will not be converted."
        )
    chat_model = config["data"]["CHAT_MODEL"]
    for name in ["backend", "web"]:
        kubectl("get", "deployment", name, "-o", "name")
    kind = shutil.which("kind") or str(
        ROOT / (".cache/bin/kind.exe" if os.name == "nt" else ".cache/bin/kind")
    )
    if not skip_build:
        run(["docker", "build", "-t", BACKEND_IMAGE, "."])
        run(["docker", "build", "-t", WEB_IMAGE, "./frontend"])
    run([kind, "load", "docker-image", BACKEND_IMAGE, WEB_IMAGE, "--name", "relay-local"])
    backup = ROOT / ".cache/kubernetes-backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
    backup.mkdir(parents=True, exist_ok=False)
    with (backup / "before-v4.dump").open("wb") as output:
        result = subprocess.run(
            [
                "kubectl",
                "--kubeconfig",
                str(KUBECONFIG),
                "--context",
                "kind-relay-local",
                "-n",
                "relay",
                "exec",
                "db-0",
                "--",
                "pg_dump",
                "-U",
                "relay",
                "-d",
                "relay",
                "-Fc",
            ],
            stdout=output,
            stderr=subprocess.PIPE,
            check=False,
        )
    if result.returncode:
        raise RuntimeError("Database snapshot failed; no application changes were applied")
    with (backup / "documents.tar.gz").open("wb") as output:
        result = subprocess.run(
            [
                "kubectl",
                "--kubeconfig",
                str(KUBECONFIG),
                "--context",
                "kind-relay-local",
                "-n",
                "relay",
                "exec",
                "deployment/backend",
                "-c",
                "api",
                "--",
                "tar",
                "czf",
                "-",
                "-C",
                "/data",
                ".",
            ],
            stdout=output,
            stderr=subprocess.PIPE,
        )
    if result.returncode:
        raise RuntimeError("Document backup failed; upgrade stopped")
    import tarfile

    with tarfile.open(backup / "documents.tar.gz", "r:gz") as archive:
        archive.getmembers()
    (backup / "configuration.json").write_text(json.dumps(config, indent=2))
    credentials = kubectl("get", "secrets", "-o", "json", capture=True)
    (backup / "secrets-private.json").write_text(credentials.stdout)
    print(f"Pre-upgrade database snapshot saved to {backup}", flush=True)
    ensure_integration_secrets()
    (backup / "secrets-after-upgrade-private.json").write_text(
        kubectl("get", "secrets", "-o", "json", capture=True).stdout
    )
    # Preserve all running configuration, override only requested grader and new private service URLs.
    config["data"].update(
        {
            "GRADER_MODEL": "gpt-5.6-terra",
            "SEARXNG_URL": "http://search:8080",
            "GMAIL_MCP_URL": "http://gmail-mcp:8101/mcp",
            "DRIVE_MCP_URL": "http://drive-mcp:8102/mcp",
        }
    )
    config["data"].pop("ALLOW_DEMO_PUBLICATION", None)
    groups = manifests()
    kubectl("scale", "deployment/backend", "--replicas=0")
    kubectl("rollout", "status", "deployment/backend", "--timeout=180s")
    kubectl("apply", "-f", "-", payload=json.dumps(config))
    search_config = next(
        r for r in groups["infrastructure"] if r["metadata"]["name"] == "relay-search-config"
    )
    kubectl("apply", "-f", "-", payload=json.dumps(search_config))
    existing_job = kubectl("get", "job", "relay-upgrade-v4", "--ignore-not-found", "-o", "json", capture=True)
    if not existing_job.stdout.strip():
        kubectl("apply", "-f", "-", payload=json.dumps(migration_job()))
    # A completed schema job is immutable. Patch releases reuse its completed migration.
    kubectl("wait", "--for=condition=complete", "job/relay-upgrade-v4", "--timeout=600s")
    kubectl(
        "apply",
        "-f",
        "-",
        payload=json.dumps({"apiVersion": "v1", "kind": "List", "items": groups["workloads"]}),
    )
    for component in ["backend", "web", "gmail-mcp", "drive-mcp", "search"]:
        kubectl("rollout", "status", "deployment/" + component, "--timeout=300s")
    active = json.loads(kubectl("get", "configmap", "relay-config", "-o", "json", capture=True).stdout)[
        "data"
    ]
    if active["CHAT_MODEL"] != chat_model or active["GRADER_MODEL"] != "gpt-5.6-terra":
        raise RuntimeError("Deployed model configuration did not match the approved upgrade")
    for attempt in range(30):
        try:
            with urlopen("http://localhost:8088/ready", timeout=5) as response:
                if response.status == 200:
                    print(
                        f"Relay updated at http://localhost:8088. Chat model preserved: {chat_model}; grader: gpt-5.6-terra. Google setup is under Connections.",
                        flush=True,
                    )
                    return
        except OSError:
            if attempt == 29:
                raise
            time.sleep(2)
    raise RuntimeError("Application readiness check did not succeed")
