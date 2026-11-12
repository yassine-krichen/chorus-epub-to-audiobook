"""Voice-cloning engine process: run by file path inside the clone-engine virtualenv.

    <clone venv>/python -I clone_worker.py --weights DIR --workdir DIR [--cache-dir DIR]
    <clone venv>/python -I clone_worker.py --probe

This file is deliberately standalone. The clone engine needs torch (CUDA), a pinned
transformers and faster-qwen3-tts, none of which may touch the main install, so it runs in a
separate virtualenv where ``book2audio`` is not installed: it imports nothing from the package
and only the standard library plus what that environment provides (numpy, soundfile, psutil,
torch, safetensors, faster_qwen3_tts).

Protocol: one JSON object per line on stdin (requests), one per line on the original stdout
(responses). Everything else the libraries print goes to stderr. Audio crosses the boundary
as 24 kHz WAV files inside ``--workdir`` (reference clips under ``refs/``, results under
``out/``); requests may only name bare file names, never paths.

    {"op": "load"}
    {"op": "synthesize", "id": 1, "text": "...", "ref_wav": "<name>.wav", "ref_text": "...",
     "ref_sha256": "<hex>", "out": "<name>.wav"}
    {"op": "unload"}
    {"op": "shutdown"}

Responses always carry ``ok``; a failure carries ``error`` (``insufficient_memory``, ``oom``,
``bad_request`` or ``failed``) and a short ``detail``.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import re
import sys
import time
from pathlib import Path

MODEL_LANGUAGE = "English"
MAX_TEXT_CHARS = 2000
_FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MB = 1024 * 1024


def _claim_protocol_stream():
    """The real stdout for protocol messages; fd 1 is pointed at stderr for everything else."""
    protocol = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return protocol


def _reply(stream, message: dict) -> None:
    stream.write(json.dumps(message, separators=(",", ":")) + "\n")
    stream.flush()


def _memory_snapshot() -> dict:
    import psutil
    import torch

    snapshot = {"ram_free_mb": int(psutil.virtual_memory().available / _MB), "cuda": False}
    if torch.cuda.is_available():
        free, total = torch.cuda.mem_get_info()
        snapshot.update(
            cuda=True,
            device=torch.cuda.get_device_name(0),
            vram_free_mb=int(free / _MB),
            vram_total_mb=int(total / _MB),
        )
    return snapshot


def _probe() -> int:
    protocol = _claim_protocol_stream()
    try:
        _reply(protocol, {"ok": True, **_memory_snapshot()})
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never crashes
        _reply(protocol, {"ok": False, "error": "failed", "detail": _short(exc)})
    return 0


def _short(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:300]


class _Engine:
    def __init__(self, weights: Path, workdir: Path, cache_dir: Path | None, min_ram_mb: int,
                 min_vram_mb: int) -> None:
        self._weights = weights
        self._refs = workdir / "refs"
        self._out = workdir / "out"
        self._cache_dir = cache_dir
        self._min_ram_mb = min_ram_mb
        self._min_vram_mb = min_vram_mb
        self._model = None
        self._prompts: dict[str, dict] = {}

    # --- model lifecycle ---------------------------------------------------

    def load(self) -> dict:
        if self._model is not None:
            return {"ok": True}
        memory = _memory_snapshot()
        if not memory["cuda"]:
            return {"ok": False, "error": "insufficient_memory", "detail": "no CUDA device"}
        if memory["ram_free_mb"] < self._min_ram_mb:
            return {
                "ok": False,
                "error": "insufficient_memory",
                "detail": f"free RAM {memory['ram_free_mb']} MB < {self._min_ram_mb} MB",
            }
        if memory["vram_free_mb"] < self._min_vram_mb:
            return {
                "ok": False,
                "error": "insufficient_memory",
                "detail": f"free VRAM {memory['vram_free_mb']} MB < {self._min_vram_mb} MB",
            }
        from faster_qwen3_tts import FasterQwen3TTS

        # Local weights only: the engine never reaches for the network.
        self._model = FasterQwen3TTS.from_pretrained(str(self._weights), local_files_only=True)
        return {"ok": True}

    def unload(self) -> dict:
        self._model = None
        self._prompts.clear()
        gc.collect()
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001 - best effort
            pass
        return {"ok": True}

    # --- synthesis ---------------------------------------------------------

    def synthesize(self, request: dict) -> dict:
        problem = _request_problem(request)
        if problem:
            return {"ok": False, "error": "bad_request", "detail": problem}
        loaded = self.load()
        if not loaded["ok"]:
            return loaded

        import soundfile as sf
        import torch

        ref_path = self._refs / request["ref_wav"]
        if not ref_path.is_file():
            return {"ok": False, "error": "bad_request", "detail": "reference clip not found"}
        started = time.perf_counter()
        try:
            samples, sample_rate = self._generate(ref_path, request)
        except torch.cuda.OutOfMemoryError as exc:
            self.unload()
            return {"ok": False, "error": "oom", "detail": _short(exc)}
        except Exception as exc:  # noqa: BLE001 - reported to the parent, which falls back
            return {"ok": False, "error": "failed", "detail": _short(exc)}
        generated_s = time.perf_counter() - started

        sf.write(str(self._out / request["out"]), samples, sample_rate, subtype="PCM_16")
        return {
            "ok": True,
            "audio_s": round(len(samples) / sample_rate, 3),
            "gen_s": round(generated_s, 3),
            "sample_rate": sample_rate,
        }

    def _generate(self, ref_path: Path, request: dict):
        import numpy as np
        import torch

        key = request["ref_sha256"]
        prompt = self._prompts.get(key) or self._load_prompt(key, request["ref_text"])
        wavs, sample_rate = self._model.generate_voice_clone(
            text=request["text"],
            language=MODEL_LANGUAGE,
            ref_audio=str(ref_path),
            ref_text=request["ref_text"],
            voice_clone_prompt=prompt,
        )
        if prompt is None:
            self._remember_prompt(key, request["ref_text"], str(ref_path))
        samples = wavs[0] if isinstance(wavs, list) else wavs
        if torch.is_tensor(samples):
            samples = samples.float().cpu().numpy()
        return np.asarray(samples, dtype=np.float32).reshape(-1), int(sample_rate)

    # --- voice prompt cache (safetensors on disk, never pickle) --------------

    def _load_prompt(self, key: str, ref_text: str) -> dict | None:
        if self._cache_dir is None:
            return None
        path = self._cache_dir / f"{key}.safetensors"
        try:
            from safetensors import safe_open

            with safe_open(str(path), framework="pt", device="cuda") as handle:
                meta = handle.metadata() or {}
                if meta.get("ref_text_sha256") != _sha256_text(ref_text):
                    return None
                prompt = {
                    "ref_code": [handle.get_tensor("ref_code")],
                    "ref_spk_embedding": [handle.get_tensor("ref_spk_embedding")],
                    "x_vector_only_mode": [False],
                    "icl_mode": [True],
                }
        except Exception:  # noqa: BLE001 - a missing or damaged entry is just a cache miss
            return None
        self._prompts[key] = prompt
        return prompt

    def _remember_prompt(self, key: str, ref_text: str, ref_path: str) -> None:
        # faster-qwen3-tts keeps the prompt it extracted in a private dict keyed by its
        # arguments; the version is pinned, and a missing entry just means no cache.
        try:
            entries = self._model._voice_prompt_cache
            prompt, _ = next(
                value for cache_key, value in entries.items() if cache_key[0] == ref_path
            )
            self._prompts[key] = prompt
            if self._cache_dir is None:
                return
            from safetensors.torch import save_file

            self._cache_dir.mkdir(parents=True, exist_ok=True)
            tensors = {
                "ref_code": prompt["ref_code"][0].contiguous().cpu(),
                "ref_spk_embedding": prompt["ref_spk_embedding"][0].contiguous().cpu(),
            }
            target = self._cache_dir / f"{key}.safetensors"
            partial = target.with_suffix(".partial")
            save_file(tensors, str(partial), metadata={"ref_text_sha256": _sha256_text(ref_text)})
            os.replace(partial, target)
        except Exception:  # noqa: BLE001 - the cache is an optimization only
            return


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _request_problem(request: dict) -> str | None:
    text, ref_text = request.get("text"), request.get("ref_text")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
        return f"text must be 1 to {MAX_TEXT_CHARS} characters"
    if not isinstance(ref_text, str) or not ref_text.strip() or len(ref_text) > MAX_TEXT_CHARS:
        return "ref_text must be a non-empty string"
    for field in ("ref_wav", "out"):
        value = request.get(field)
        if not isinstance(value, str) or not _FILE_NAME.fullmatch(value):
            return f"{field} must be a bare file name"
    sha = request.get("ref_sha256")
    if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
        return "ref_sha256 must be 64 hex characters"
    return None


def _serve(args: argparse.Namespace) -> int:
    protocol = _claim_protocol_stream()
    workdir = Path(args.workdir)
    (workdir / "out").mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    engine = _Engine(
        Path(args.weights), workdir, cache_dir, args.min_free_ram_mb, args.min_free_vram_mb
    )
    _reply(protocol, {"ok": True, "ready": True})
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
        except ValueError:
            _reply(protocol, {"ok": False, "error": "bad_request", "detail": "not a JSON object"})
            continue
        op = request.get("op")
        if op == "shutdown":
            _reply(protocol, {"ok": True})
            return 0
        if op == "load":
            response = engine.load()
        elif op == "unload":
            response = engine.unload()
        elif op == "synthesize":
            response = engine.synthesize(request)
        else:
            response = {"ok": False, "error": "bad_request", "detail": "unknown op"}
        if "id" in request:
            response["id"] = request["id"]
        _reply(protocol, response)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--weights")
    parser.add_argument("--workdir")
    parser.add_argument("--cache-dir")
    parser.add_argument("--min-free-ram-mb", type=int, default=3000)
    parser.add_argument("--min-free-vram-mb", type=int, default=3300)
    args = parser.parse_args()
    if args.probe:
        return _probe()
    if not args.weights or not args.workdir:
        parser.error("--weights and --workdir are required")
    return _serve(args)


if __name__ == "__main__":
    sys.exit(main())
