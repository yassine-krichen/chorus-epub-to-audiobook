"""Cloned-voice synthesis: a thin client for the clone engine process, and the routing engine.

The clone engine (Qwen3-TTS 0.6B Base through faster-qwen3-tts) needs torch with CUDA, a
pinned transformers and a few hundred MB of dependencies that must not touch the base install.
It runs as a separate process, ``clone_worker.py``, inside its own virtualenv. This module
never imports torch: it starts that process, talks to it over JSON lines, and validates
everything that comes back.

``RoutingEngine`` is what the pipeline uses when a job names a cloned voice. It sends
``clone:<uuid>`` voices to the clone engine and everything else to Kokoro, and falls back to
a Kokoro voice of the same gender whenever cloning is impossible or fails, so that cloning
can never make a chapter fail.

Setup (see docs/features/voice-cloning.md): ``CLONE_PYTHON`` is the clone virtualenv's
python, ``CLONE_WEIGHTS_DIR`` the local Qwen3-TTS weights.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
import soundfile as sf

from book2audio.tts.engine import SAMPLE_RATE, TtsEngine, chunk_text
from book2audio.tts.voices import DEFAULT_NARRATOR_VOICE, FEMALE_VOICES, is_clone_voice

logger = logging.getLogger(__name__)

# The capability a worker advertises when it can run jobs that use a cloned voice.
CLONE_CAPABILITY = "clone:qwen3"

_WORKER_SCRIPT = Path(__file__).with_name("clone_worker.py")
_PROBE_TIMEOUT_S = 180
_LOAD_TIMEOUT_S = 600
_MAX_REPLY_BYTES = 64 * 1024
_MAX_OUTPUT_BYTES = 60 * 1024 * 1024
# A 350-character chunk is well under a minute of speech; far more means a runaway.
_MAX_CHUNK_AUDIO_S = 180.0
_MAX_PROMPT_TEXT_CHARS = 2000
# Same default for the fallback as the app's own: a male narrator-style voice and the first
# female character voice. Only used when a cloned voice cannot be spoken.
_FALLBACK_VOICE = {"m": DEFAULT_NARRATOR_VOICE, "f": FEMALE_VOICES[0]}


# The only reasons a user ever sees for a cloned voice being replaced by a Kokoro voice. They
# are fixed sentences: what the engine or the libraries said (paths, tracebacks, memory sizes)
# goes to the worker's log and nowhere else, because the reason travels to the cast report
# and from there to the website.
REASON_OOM = "the voice-cloning engine ran out of memory"
REASON_STOPPED = "the voice-cloning engine stopped"
REASON_NOT_INSTALLED = "the voice-cloning engine is not installed"
REASON_TIMEOUT = "the voice-cloning engine timed out"
REASON_RECORDING = "the voice recording could not be used"
REASON_VOICE_GONE = "the voice no longer exists"
FALLBACK_REASONS = frozenset(
    {
        REASON_OOM,
        REASON_STOPPED,
        REASON_NOT_INSTALLED,
        REASON_TIMEOUT,
        REASON_RECORDING,
        REASON_VOICE_GONE,
    }
)


class CloneEngineError(RuntimeError):
    """The clone engine could not produce audio; the caller falls back to Kokoro.

    ``str(error)`` is the technical detail, for logs only. ``reason`` is one of
    :data:`FALLBACK_REASONS`, the only part that may be shown to a user.
    """

    def __init__(self, detail: str, *, reason: str = REASON_STOPPED) -> None:
        super().__init__(detail)
        self.reason = reason


@dataclass(frozen=True)
class CloneReference:
    """One cloned voice as the engine needs it: the clip to imitate and what is said in it."""

    voice_id: str  # clone:<uuid>
    wav_path: str  # local copy of the normalized reference clip
    ref_text: str  # the passage read in the clip
    sha256: str  # of the clip's bytes; keys the prompt cache
    gender: Literal["m", "f"] = "m"  # picks the Kokoro fallback voice


@dataclass(frozen=True)
class CloneCapability:
    available: bool
    reason: str = ""
    device: str | None = None


# --- configuration ---------------------------------------------------------


def _clone_python() -> str | None:
    return os.environ.get("CLONE_PYTHON") or None


def _weights_dir() -> str | None:
    return os.environ.get("CLONE_WEIGHTS_DIR") or None


def prompt_cache_dir() -> Path:
    override = os.environ.get("CLONE_PROMPT_CACHE_DIR")
    return Path(override) if override else Path.home() / ".cache" / "book2audio" / "clone-prompts"


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def probe_clone_capability() -> CloneCapability:
    """Whether this machine can synthesize cloned voices, without loading the model.

    Needs ``CLONE_PYTHON`` and ``CLONE_WEIGHTS_DIR`` to point at real things, and the clone
    environment to see a CUDA device (CPU is about 15 times too slow to be worth it).
    """
    python, weights = _clone_python(), _weights_dir()
    if not python or not weights:
        return CloneCapability(False, "CLONE_PYTHON and CLONE_WEIGHTS_DIR are not set")
    if not os.path.isfile(python):
        return CloneCapability(False, f"CLONE_PYTHON does not exist: {python}")
    weights_path = Path(weights)
    if not (weights_path / "config.json").is_file() or not (weights_path / "model.safetensors").is_file():
        return CloneCapability(False, f"no Qwen3-TTS weights in {weights}")
    try:
        result = subprocess.run(
            [python, "-I", str(_WORKER_SCRIPT), "--probe"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
        reply = json.loads(result.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        return CloneCapability(False, f"the clone environment did not start: {type(exc).__name__}")
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        return CloneCapability(False, "the clone environment is missing its libraries")
    if reply.get("cuda") is not True:
        return CloneCapability(False, "no CUDA GPU (cloning on CPU is too slow to use)")
    return CloneCapability(True, device=str(reply.get("device", ""))[:80] or None)


def prune_prompt_cache(keep_sha256: set[str], cache_dir: Path | None = None) -> int:
    """Delete cached voice prompts whose reference clip no longer exists; returns how many."""
    directory = cache_dir or prompt_cache_dir()
    removed = 0
    if not directory.is_dir():
        return 0
    for entry in directory.glob("*.safetensors"):
        if entry.stem not in keep_sha256:
            entry.unlink(missing_ok=True)
            removed += 1
    return removed


# --- the engine process client -------------------------------------------------


class QwenCloneEngine:
    """Synthesizes speech in a cloned voice by driving the clone engine process.

    The process (and with it the model on the GPU) starts on the first request and ends on
    :meth:`close`, so the GPU is free again as soon as the job is done.
    """

    def __init__(self, python: str, weights_dir: str) -> None:
        self._python = python
        self._weights = weights_dir
        self._workdir = Path(tempfile.mkdtemp(prefix="b2a-clone-"))
        (self._workdir / "refs").mkdir()
        (self._workdir / "out").mkdir()
        self._process: subprocess.Popen[str] | None = None
        self._replies: queue.Queue[str | None] = queue.Queue()
        self._stderr_path = self._workdir / "engine.log"
        self._next_id = 0
        self._staged: set[str] = set()
        self.synth_timeout_s = _int_env("CLONE_SYNTH_TIMEOUT_S", 300)

    # --- public ------------------------------------------------------------

    def synthesize_clone(self, text: str, reference: CloneReference) -> np.ndarray:
        """Mono 24 kHz samples of ``text`` in the cloned voice; raises :class:`CloneEngineError`."""
        if not text.strip():
            return np.zeros(0, dtype=np.float32)
        ref_name = self._stage_reference(reference)
        pieces = [self._synthesize_chunk(chunk, reference, ref_name) for chunk in chunk_text(text)]
        return np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)

    def close(self) -> None:
        """Stop the engine process, which frees its GPU memory, and remove the temp files."""
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            try:
                self._send({"op": "shutdown"}, process)
                process.wait(timeout=10)
            except (OSError, subprocess.SubprocessError):
                process.kill()
        if process is not None and process.poll() is None:
            process.kill()
        shutil.rmtree(self._workdir, ignore_errors=True)

    # --- internals ---------------------------------------------------------

    def _stage_reference(self, reference: CloneReference) -> str:
        name = f"{reference.sha256}.wav"
        target = self._workdir / "refs" / name
        if reference.sha256 not in self._staged:
            digest = hashlib.sha256(Path(reference.wav_path).read_bytes()).hexdigest()
            if digest != reference.sha256:
                raise CloneEngineError(
                    "the reference clip does not match its recorded hash", reason=REASON_RECORDING
                )
            shutil.copyfile(reference.wav_path, target)
            self._staged.add(reference.sha256)
        return name

    def _synthesize_chunk(self, text: str, reference: CloneReference, ref_name: str) -> np.ndarray:
        process = self._ensure_started()
        self._next_id += 1
        request_id = self._next_id
        out_name = f"chunk-{request_id}.wav"
        reply = self._request(
            process,
            {
                "op": "synthesize",
                "id": request_id,
                "text": text,
                "ref_wav": ref_name,
                "ref_text": reference.ref_text[:_MAX_PROMPT_TEXT_CHARS],
                "ref_sha256": reference.sha256,
                "out": out_name,
            },
            timeout=self.synth_timeout_s,
        )
        if reply.get("id") != request_id:
            raise CloneEngineError("the clone engine answered a different request")
        out_path = self._workdir / "out" / out_name
        try:
            return _read_result(out_path)
        finally:
            out_path.unlink(missing_ok=True)

    def _ensure_started(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        command = [
            self._python,
            "-I",
            str(_WORKER_SCRIPT),
            "--weights",
            self._weights,
            "--workdir",
            str(self._workdir),
            "--cache-dir",
            str(prompt_cache_dir()),
            "--min-free-ram-mb",
            str(_int_env("CLONE_MIN_FREE_RAM_MB", 3000)),
            "--min-free-vram-mb",
            str(_int_env("CLONE_MIN_FREE_VRAM_MB", 3300)),
        ]
        with open(self._stderr_path, "ab") as log:
            try:
                process = subprocess.Popen(  # noqa: S603 - our own script, fixed argv
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                )
            except OSError as exc:
                raise CloneEngineError(
                    f"could not start the clone engine: {exc}", reason=REASON_NOT_INSTALLED
                ) from exc
        self._process = process
        self._replies = queue.Queue()
        threading.Thread(target=_pump_lines, args=(process, self._replies), daemon=True).start()
        ready = self._read_reply(process, timeout=_LOAD_TIMEOUT_S)
        if ready.get("ready") is not True:
            self._discard(process)
            raise CloneEngineError("the clone engine did not report ready")
        return process

    def _request(self, process: subprocess.Popen[str], message: dict, timeout: float) -> dict:
        self._send(message, process)
        reply = self._read_reply(process, timeout=timeout)
        if reply.get("ok") is not True:
            error = str(reply.get("error", "failed"))[:40]
            detail = str(reply.get("detail", ""))[:200]
            if error in ("oom", "insufficient_memory"):
                self._discard(process)
            raise CloneEngineError(
                f"{error}: {detail}" if detail else error,
                reason=_REASON_FOR_ENGINE_ERROR.get(error, REASON_STOPPED),
            )
        return reply

    @staticmethod
    def _send(message: dict, process: subprocess.Popen[str]) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def _read_reply(self, process: subprocess.Popen[str], timeout: float) -> dict:
        try:
            line = self._replies.get(timeout=timeout)
        except queue.Empty:
            self._discard(process)
            raise CloneEngineError("the clone engine timed out", reason=REASON_TIMEOUT) from None
        if line is None:
            self._discard(process)
            raise CloneEngineError(f"the clone engine stopped unexpectedly{self._log_tail()}")
        if len(line) > _MAX_REPLY_BYTES:
            self._discard(process)
            raise CloneEngineError("the clone engine sent an oversized reply")
        try:
            reply = json.loads(line)
        except ValueError:
            raise CloneEngineError("the clone engine sent a reply that is not JSON") from None
        if not isinstance(reply, dict):
            raise CloneEngineError("the clone engine sent a reply that is not an object")
        return reply

    def _discard(self, process: subprocess.Popen[str]) -> None:
        if self._process is process:
            self._process = None
        if process.poll() is None:
            process.kill()

    def _log_tail(self) -> str:
        try:
            tail = self._stderr_path.read_text(encoding="utf-8", errors="replace")[-300:]
        except OSError:
            return ""
        return f" ({tail.strip().splitlines()[-1]})" if tail.strip() else ""


# What the engine process calls a failure, mapped to what a user may be told.
_REASON_FOR_ENGINE_ERROR = {
    "oom": REASON_OOM,
    "insufficient_memory": REASON_OOM,
    "bad_request": REASON_RECORDING,
}


def _pump_lines(process: subprocess.Popen[str], replies: queue.Queue[str | None]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        replies.put(line)
    replies.put(None)  # the stream ended: the process is gone


def _read_result(path: Path) -> np.ndarray:
    """Decode an engine result, refusing anything that is not plausible 24 kHz mono speech."""
    try:
        size = path.stat().st_size
        if size > _MAX_OUTPUT_BYTES:
            raise CloneEngineError("the clone engine wrote an oversized file")
        samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=False)
    except (OSError, RuntimeError) as exc:
        raise CloneEngineError(f"the clone engine's audio could not be read: {exc}") from exc
    if sample_rate != SAMPLE_RATE:
        raise CloneEngineError(f"unexpected sample rate {sample_rate}")
    if samples.ndim != 1:
        raise CloneEngineError("the clone engine's audio is not mono")
    if len(samples) / sample_rate > _MAX_CHUNK_AUDIO_S or not np.isfinite(samples).all():
        raise CloneEngineError("the clone engine's audio is not plausible")
    return samples.astype(np.float32, copy=False)


def load_clone_engine() -> QwenCloneEngine | None:
    """A clone engine for this machine, or ``None`` when it is not set up (see the setup docs)."""
    python, weights = _clone_python(), _weights_dir()
    if not python or not weights:
        return None
    return QwenCloneEngine(python, weights)


# --- routing + fallback ----------------------------------------------------


@dataclass
class RoutingEngine:
    """Kokoro for Kokoro voices, the clone engine for ``clone:<uuid>`` ones, Kokoro for the rest.

    A cloned voice is spoken by the clone engine only when it has a reference clip and an
    engine; otherwise, or when the engine fails (out of memory, crash, timeout), a Kokoro
    voice of the voice's gender reads instead. After a failure the engine is not tried again
    for the rest of the job. ``fallbacks`` records, per cloned voice, why it was replaced.
    """

    kokoro: TtsEngine
    references: dict[str, CloneReference]
    clone: QwenCloneEngine | None = None
    fallbacks: dict[str, str] = field(default_factory=dict)
    # Parts read by Kokoro because an engine that was expected to work failed.
    fallback_count: int = 0
    _clone_failed: bool = False
    _failure_reason: str = REASON_STOPPED

    def synthesize(self, text: str, voice: str, speed: float = 1.0) -> np.ndarray:
        if not is_clone_voice(voice):
            return self.kokoro.synthesize(text, voice, speed)
        reference = self.references.get(voice)
        usable = reference is not None and self.clone is not None
        if usable and not self._clone_failed:
            try:
                # The clone model has no speed control: ``speed`` does not apply to it.
                return self.clone.synthesize_clone(text, reference)
            except CloneEngineError as exc:
                self._clone_failed = True
                self._failure_reason = exc.reason
                self._record_fallback(voice, exc.reason)
                # The technical detail stays in the worker's log.
                logger.warning("clone.engine_failed", extra={"voice": voice}, exc_info=True)
        else:
            self._record_fallback(voice, self._why_unavailable(reference))
        if usable:
            # Only a failure of an engine that was expected to work counts: a voice that was
            # never going to be cloned (no clip, no engine) is known before the render starts.
            self.fallback_count += 1
        return self.kokoro.synthesize(text, self.fallback_voice(voice), speed)

    def resolve_voice(self, voice: str) -> str:
        """The voice that would actually speak ``voice`` right now (for the chapter hash)."""
        if not is_clone_voice(voice):
            return voice
        usable = voice in self.references and self.clone is not None and not self._clone_failed
        return voice if usable else self.fallback_voice(voice)

    def fallback_voice(self, voice: str) -> str:
        reference = self.references.get(voice)
        return self.fallback_for_gender(reference.gender if reference is not None else "m")

    @staticmethod
    def fallback_for_gender(gender: str) -> str:
        return _FALLBACK_VOICE["f" if gender == "f" else "m"]

    def close(self) -> None:
        if self.clone is not None:
            self.clone.close()

    def _why_unavailable(self, reference: CloneReference | None) -> str:
        if reference is None:
            return REASON_VOICE_GONE
        if self.clone is None:
            return REASON_NOT_INSTALLED
        return self._failure_reason  # the engine failed earlier in this job

    def _record_fallback(self, voice: str, reason: str) -> None:
        self.fallbacks.setdefault(voice, reason)
