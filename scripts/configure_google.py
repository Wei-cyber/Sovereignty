"""Configure only Google settings in the dedicated local cluster; never log credentials."""

import json
import os
from scripts.deploy_kubernetes import kubectl


def main():
    names = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_PICKER_API_KEY", "GOOGLE_PROJECT_NUMBER"]
    if any(not os.environ.get(name) for name in names):
        raise SystemExit(
            "Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_PICKER_API_KEY and GOOGLE_PROJECT_NUMBER in your private shell."
        )
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "relay-integrations", "namespace": "relay"},
        "stringData": {name: os.environ[name] for name in names},
    }
    result = kubectl(
        "apply",
        "--server-side",
        "--field-manager=relay-google-setup",
        "--force-conflicts",
        "-f",
        "-",
        payload=json.dumps(secret),
        capture=True,
        check=False,
    )
    if result.returncode:
        raise SystemExit("Google configuration failed; credential details withheld.")
    for name in ["backend", "gmail-mcp", "drive-mcp"]:
        kubectl("rollout", "restart", "deployment/" + name)
        kubectl("rollout", "status", "deployment/" + name, "--timeout=300s")
    print("Google settings updated. Open Connections in Relay.")


if __name__ == "__main__":
    main()
