"""Verify backup checksums and inspect archive paths without changing a deployment."""

import argparse
import hashlib
import json
import tarfile
from pathlib import Path, PurePosixPath


def verify(directory):
    root = Path(directory).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if set(manifest) != {"database.dump", "documents.tar.gz"}:
        raise ValueError("Backup manifest has unexpected files")
    for name, expected in manifest.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Checksum mismatch: {name}")
    with tarfile.open(root / "documents.tar.gz", "r:gz") as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise ValueError("Unsafe archive entry")
            if (
                not (member.isfile() or member.isdir())
                or not path.parts
                or (path.parts[0] != "documents" and member.name != "grader-calibration.json")
            ):
                raise ValueError("Unexpected archive entry")
    print("Backup checksums and document archive structure are valid.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    verify(parser.parse_args().directory)
