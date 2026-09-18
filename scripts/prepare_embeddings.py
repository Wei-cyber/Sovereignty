"""Download a pinned public BGE artifact once; inference thereafter is offline."""

import hashlib
import json

import httpx

from backend.config import settings
from backend.embedding_spec import BGE_MODEL, BGE_REVISION


def main():
    directory = settings().embedding_model_dir
    directory.mkdir(parents=True, exist_ok=True)
    hashes = {}
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        for name in ("tokenizer.json", "onnx/model.onnx", "README.md"):
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".download")
            with client.stream(
                "GET", f"https://huggingface.co/{BGE_MODEL}/resolve/{BGE_REVISION}/{name}"
            ) as response:
                response.raise_for_status()
                with temporary.open("wb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
            temporary.replace(path)
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (directory / "manifest.json").write_text(
        json.dumps({"model": BGE_MODEL, "revision": BGE_REVISION, "sha256": hashes}, indent=2)
    )
    print(f"Prepared {BGE_MODEL} at {BGE_REVISION}. Local inference needs no network or API key.")


if __name__ == "__main__":
    main()
