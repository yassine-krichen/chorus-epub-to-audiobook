"""Text-to-speech: cast voices and synthesize segments with Kokoro."""

from book2audio.tts.engine import KokoroEngine
from book2audio.tts.voices import VoiceCast

__all__ = ["KokoroEngine", "VoiceCast"]
