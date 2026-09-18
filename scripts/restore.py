"""Restore only into a NEW, isolated Compose project. Existing deployments are refused."""

import argparse
import importlib.util
import re
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("--env-file", default=".env.compose")
    parser.add_argument("--project", required=True, help="New project name beginning relay-restore-")
    args = parser.parse_args()
    if not re.fullmatch(r"relay-restore-[a-z0-9-]+", args.project):
        raise SystemExit("Choose a NEW project name beginning relay-restore-.")
    spec = importlib.util.spec_from_file_location(
        "verify_backup", Path(__file__).with_name("verify-backup.py")
    )
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    verifier.verify(args.directory)
    for kind in ["ps", "volume"]:
        command = ["docker", "ps", "-aq"] if kind == "ps" else ["docker", "volume", "ls", "-q"]
        existing = subprocess.check_output(
            command + ["--filter", f"label=com.docker.compose.project={args.project}"], text=True
        ).strip()
        if existing:
            raise SystemExit("This project already has containers or volumes. Refusing to overwrite it.")
    base = ["docker", "compose", "--env-file", args.env_file, "--project-name", args.project]

    def compose(*command, **kwargs):
        return subprocess.run(base + list(command), check=True, **kwargs)

    root = Path(args.directory).resolve()
    compose("up", "-d", "--wait", "db", "redis")
    with (root / "database.dump").open("rb") as data:
        compose(
            "exec",
            "-T",
            "db",
            "pg_restore",
            "-U",
            "relay",
            "-d",
            "relay",
            "--no-owner",
            "--exit-on-error",
            stdin=data,
        )
    with (root / "documents.tar.gz").open("rb") as data:
        compose(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "api",
            "python",
            "-c",
            "import sys,tarfile; t=tarfile.open(fileobj=sys.stdin.buffer,mode='r|gz'); t.extractall('/data',filter='data')",
            stdin=data,
        )
    compose("run", "--rm", "-T", "migrate")
    compose("up", "-d", "--wait", "api", "worker", "scheduler")
    compose(
        "exec",
        "-T",
        "api",
        "python",
        "-c",
        "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/ready').read().decode())",
    )
    print(
        f"Restore smoke check passed in isolated project {args.project}. The original deployment was not modified."
    )
    print("Inspect document contents, citations, and a new run before declaring disaster recovery verified.")


if __name__ == "__main__":
    main()
