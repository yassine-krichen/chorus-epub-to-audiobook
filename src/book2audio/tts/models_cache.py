"""Download and cache the Kokoro ONNX model + voices files.

These two files (~325 MB total) are fetched once from the kokoro-onnx release
assets and cached under the user cache dir, so re-runs and other books reuse
them. Downloads are streamed and written atomically to survive interruption.
"""

from __future__ import annotations

import os

import httpx
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TransferSpeedColumn,
)

_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
_FILES = {
    "kokoro-v1.0.onnx": f"{_RELEASE}/kokoro-v1.0.onnx",
    "voices-v1.0.bin": f"{_RELEASE}/voices-v1.0.bin",
}


def cache_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.cache")
    path = os.path.join(base, "book2audio", "models")
    os.makedirs(path, exist_ok=True)
    return path


def ensure_model_files() -> tuple[str, str]:
    """Return (model_path, voices_path), downloading any missing file first."""
    directory = cache_dir()
    paths: dict[str, str] = {}
    for name, url in _FILES.items():
        dest = os.path.join(directory, name)
        if not os.path.exists(dest) or os.path.getsize(dest) == 0:
            _download(url, dest, name)
        paths[name] = dest
    return paths["kokoro-v1.0.onnx"], paths["voices-v1.0.bin"]


def _download(url: str, dest: str, label: str) -> None:
    tmp = dest + ".part"
    with httpx.stream("GET", url, follow_redirects=True, timeout=None) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length", 0)) or None
        with Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
        ) as progress:
            task = progress.add_task(f"Downloading {label}", total=total)
            with open(tmp, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=1 << 20):
                    f.write(chunk)
                    progress.update(task, advance=len(chunk))
    os.replace(tmp, dest)  # atomic: a partial download never looks complete
