"""Kokoro TTS wrapper: text + voice -> mono float32 audio at 24 kHz.

Kokoro synthesizes a bounded number of tokens per call, and very long inputs
degrade in quality, so we split each segment on sentence boundaries into
modest chunks and concatenate. Loading the model is expensive, so callers
should create one engine and reuse it for the whole book.
"""

from __future__ import annotations

import re

import numpy as np

from book2audio.tts.models_cache import ensure_model_files

SAMPLE_RATE = 24_000

# Split on sentence-ending punctuation while keeping the punctuation attached.
_SENTENCE = re.compile(r"[^.!?…]+[.!?…]*\s*")

# Soft cap on characters per synthesis call. Well under Kokoro's token limit so
# phoneme expansion never overflows, while still giving natural prosody.
_MAX_CHARS = 350


class KokoroEngine:
    def __init__(self, lang: str = "en-us") -> None:
        # Imported lazily so `--help` and unit tests don't pay the import cost.
        from kokoro_onnx import Kokoro

        model_path, voices_path = ensure_model_files()
        self._kokoro = Kokoro(model_path, voices_path)
        self._lang = lang

    def synthesize(self, text: str, voice: str, speed: float = 1.0) -> np.ndarray:
        """Return mono float32 samples for ``text`` in ``voice``.

        Empty/blank text yields an empty array so callers can concatenate freely.
        """
        if not text.strip():
            return np.zeros(0, dtype=np.float32)

        pieces: list[np.ndarray] = []
        for chunk in self._chunk(text):
            samples, _ = self._kokoro.create(chunk, voice=voice, speed=speed, lang=self._lang)
            pieces.append(np.asarray(samples, dtype=np.float32))
        return np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)

    @staticmethod
    def _chunk(text: str) -> list[str]:
        sentences = _SENTENCE.findall(text) or [text]
        chunks: list[str] = []
        current = ""
        for sentence in sentences:
            if current and len(current) + len(sentence) > _MAX_CHARS:
                chunks.append(current.strip())
                current = sentence
            else:
                current += sentence
        if current.strip():
            chunks.append(current.strip())
        return chunks
