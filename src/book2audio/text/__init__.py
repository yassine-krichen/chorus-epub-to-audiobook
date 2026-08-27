"""Text stage: turn raw paragraphs into attributed, speakable segments."""

from book2audio.text.expressiveness import speech_params
from book2audio.text.normalize import normalize_for_tts
from book2audio.text.segmenter import segment_paragraphs

__all__ = ["segment_paragraphs", "normalize_for_tts", "speech_params"]
