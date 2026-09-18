"""Create or resume Relay's private single-node kind deployment. Never targets the default context."""

import argparse
import json
import os
import secrets
import shutil
import subprocess
import time

from scripts.kubernetes_manifests import BACKEND_IMAGE, WEB_IMAGE, ROOT, main as render

KUBECONFIG = ROOT / ".cache/kubeconfig-relay"


def check_docker(wait_seconds=30):
    """Wait briefly for Desktop startup; keep raw diagnostics out of deployment logs."""
    deadline = time.monotonic() + wait_seconds
    announced = False
    while True:
        try:
            result = subprocess.run(
                ["docker", "info", "--format", "{{.OSType}}"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=max(1, min(10, deadline - time.monotonic())),
            )
        except FileNotFoundError:
            raise RuntimeError(
                "Docker is not on PATH. Install/open Docker Desktop, then reopen PowerShell."
            ) from None
        except subprocess.TimeoutExpired:
            result = None
        if result is not None and "manually paused" in (result.stderr + result.stdout).lower():
            raise RuntimeError(
                "Docker Desktop is paused. Unpause it in the dashboard or whale menu, then retry. "
                "No Kubernetes changes were made."
            )
        if result is not None and result.returncode == 0:
            if result.stdout.strip() != "linux":
                raise RuntimeError(
                    "Relay requires Linux containers. Switch Docker Desktop to Linux containers and retry."
                )
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Docker is installed, but its Linux engine is unavailable. Open Docker Desktop and wait for "
                "the engine to be running, then retry:\n"
                "  uv run python -m scripts.deploy_kubernetes --skip-build\n"
                "If Docker Desktop reports a startup error, resolve it there first. "
                "Run 'docker info' for Docker's diagnostic. No Kubernetes changes were made."
            )
        if not announced:
            print("Waiting for Docker's Linux engine to become available...", flush=True)
            announced = True
        time.sleep(min(2, max(0, deadline - time.monotonic())))


def run(args, *, payload=None, capture=False, check=True):
    result = subprocess.run(args, cwd=ROOT, input=payload, text=True, capture_output=capture, check=False)
    if check and result.returncode:
        raise RuntimeError(f"Command failed: {args[0]} {args[1]} (exit {result.returncode})")
    return result


def kubectl(*args, **kwargs):
    return run(
        ["kubectl", "--kubeconfig", str(KUBECONFIG), "--context", "kind-relay-local", "-n", "relay", *args],
        **kwargs,
    )


def ensure_secret(name, values):
    existing = kubectl("get", "secret", name, "--ignore-not-found", "-o", "name", capture=True)
    if existing.stdout.strip():
        print(f"Keeping existing {name} credentials.", flush=True)
        return
    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": "relay"},
        "type": "Opaque",
        "stringData": values,
    }
    result = kubectl("create", "-f", "-", payload=json.dumps(body), capture=True, check=False)
    if result.returncode:
        raise RuntimeError(f"Could not create {name}; credential payload has been withheld from logs")
    print(f"Created {name} credentials.", flush=True)


def ensure_integration_secrets():
    from cryptography.fernet import Fernet

    ensure_secret(
        "relay-integrations",
        {
            "CONNECTION_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "MCP_SIGNING_KEY": secrets.token_urlsafe(48),
            "SEARXNG_SECRET": secrets.token_urlsafe(48),
            **{
                name: os.environ.get(name, "")
                for name in [
                    "GOOGLE_CLIENT_ID",
                    "GOOGLE_CLIENT_SECRET",
                    "GOOGLE_PICKER_API_KEY",
                    "GOOGLE_PROJECT_NUMBER",
                ]
            },
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-cluster", action="store_true")
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="Update existing application images and schema while preserving mode, configuration, accounts, and secrets",
    )
    parser.add_argument(
        "--live", action="store_true", help="Use GPT-5.6 and local BGE; requires server credentials"
    )
    parser.add_argument(
        "--docker-wait", type=int, default=30, help="Seconds to wait for Docker startup (0-300)"
    )
    args = parser.parse_args()
    if not 0 <= args.docker_wait <= 300:
        parser.error("--docker-wait must be between 0 and 300 seconds")
    if args.upgrade:
        from scripts.upgrade_kubernetes import upgrade

        check_docker(args.docker_wait)
        upgrade(args.skip_build)
        return
    args.live = True
    bootstrap_email = os.environ.get("BOOTSTRAP_EMAIL", "admin@example.test")
    bootstrap_password = os.environ.get("BOOTSTRAP_PASSWORD", "")
    if args.live and (
        not os.environ.get("OPENAI_API_KEY")
        or len(bootstrap_password) < 12
        or bootstrap_email.endswith(".test")
    ):
        parser.error(
            "Live mode requires OPENAI_API_KEY, a real BOOTSTRAP_EMAIL and a strong BOOTSTRAP_PASSWORD"
        )
    os.chdir(ROOT)
    kind = shutil.which("kind") or str(
        ROOT / (".cache/bin/kind.exe" if os.name == "nt" else ".cache/bin/kind")
    )
    check_docker(args.docker_wait)
    if not args.skip_cluster:
        clusters = run([kind, "get", "clusters"], capture=True).stdout.splitlines()
        if "relay-local" not in clusters:
            run(
                [
                    kind,
                    "create",
                    "cluster",
                    "--name",
                    "relay-local",
                    "--config",
                    "ops/kubernetes/kind.yaml",
                    "--kubeconfig",
                    str(KUBECONFIG),
                    "--wait",
                    "180s",
                ]
            )
        elif not KUBECONFIG.exists():
            run([kind, "export", "kubeconfig", "--name", "relay-local", "--kubeconfig", str(KUBECONFIG)])
    kubectl("get", "nodes")
    existing = kubectl("get", "configmap", "relay-config", "--ignore-not-found", "-o", "json", capture=True)
    if existing.stdout.strip():
        from scripts.upgrade_kubernetes import upgrade

        upgrade(args.skip_build)
        return
    if not args.skip_build:
        run(["docker", "build", "-t", BACKEND_IMAGE, "."])
        run(["docker", "build", "-t", WEB_IMAGE, "./frontend"])
    run([kind, "load", "docker-image", BACKEND_IMAGE, WEB_IMAGE, "--name", "relay-local"])
    folder = render(args.live, bootstrap_email)
    # Infrastructure can reconcile while credentials and volumes are being provisioned.
    kubectl("apply", "-f", str(folder / "infrastructure.json"))
    admin_password, app_password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    ensure_secret(
        "relay-database",
        {
            "admin-password": admin_password,
            "application-password": app_password,
            "migration-url": f"postgresql+psycopg://relay:{admin_password}@db:5432/relay",
            "application-url": f"postgresql+psycopg://relay_app:{app_password}@db:5432/relay",
        },
    )
    ensure_secret("relay-openai", {"api-key": os.environ["OPENAI_API_KEY"]})
    ensure_secret("relay-live-bootstrap", {"bootstrap-password": bootstrap_password})
    ensure_integration_secrets()
    for name in ["db", "redis"]:
        kubectl("rollout", "status", f"statefulset/{name}", "--timeout=300s")
    kubectl("apply", "-f", str(folder / "setup.json"))
    setup_job = "relay-setup-v4"
    kubectl("wait", "--for=condition=complete", f"job/{setup_job}", "--timeout=600s")
    kubectl("apply", "-f", str(folder / "workloads.json"))
    for name in ["backend", "web"]:
        kubectl("rollout", "status", f"deployment/{name}", "--timeout=300s")
    import urllib.request

    for attempt in range(30):
        try:
            with urllib.request.urlopen("http://localhost:8088/ready", timeout=5) as response:
                if response.status == 200:
                    break
        except OSError:
            if attempt == 29:
                raise
            time.sleep(2)
    kubectl("get", "pods,pvc,services")
    print("Relay is ready at http://localhost:8088. Google connections require operator setup.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
