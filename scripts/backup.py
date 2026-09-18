"""Create a quiesced, checksummed backup from the local Docker deployment."""

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def compose(env_file, *args, **kwargs):
    return subprocess.run(["docker", "compose", "--env-file", env_file, *args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.compose")
    parser.add_argument("--output", default="artifacts/backups")
    args = parser.parse_args()
    target = Path(args.output).resolve() / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target.mkdir(parents=True, exist_ok=False)
    stopped = False
    try:
        stopped = True
        compose(args.env_file, "stop", "web", "api", "scheduler", "worker", "--timeout", "70")
        with (target / "database.dump").open("wb") as output:
            compose(
                args.env_file,
                "exec",
                "-T",
                "db",
                "pg_dump",
                "-U",
                "relay",
                "-d",
                "relay",
                "-Fc",
                stdout=output,
            )
        compose(
            args.env_file,
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "api",
            "python",
            "-c",
            "import tarfile; from pathlib import Path; from backend.config import settings; Path('/data/documents').mkdir(exist_ok=True); t=tarfile.open('/data/documents-backup.tar.gz','w:gz'); t.add('/data/documents',arcname='documents'); t.add(settings().grader_calibration_file,arcname='grader-calibration.json') if settings().grader_calibration_file else None; t.close()",
        )
        with (target / "documents.tar.gz").open("wb") as output:
            compose(
                args.env_file,
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "api",
                "python",
                "-c",
                "import sys; from pathlib import Path; sys.stdout.buffer.write(Path('/data/documents-backup.tar.gz').read_bytes())",
                stdout=output,
            )
        manifest = {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in target.iterdir()}
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Backup created: {target}")
    finally:
        if stopped:
            compose(args.env_file, "start", "worker", "scheduler", "api", "web")


if __name__ == "__main__":
    main()
