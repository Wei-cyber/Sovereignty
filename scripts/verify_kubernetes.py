"""Read-only Kubernetes readiness check without sign-in credentials."""

from urllib.request import urlopen
from scripts.deploy_kubernetes import kubectl

if __name__ == "__main__":
    kubectl("get", "pods,pvc,services")
    with urlopen("http://localhost:8088/ready", timeout=10) as response:
        print("Relay is ready" if response.status == 200 else "Relay is not ready")
