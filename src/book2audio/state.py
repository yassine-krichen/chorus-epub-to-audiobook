"""Resume state: which chapters are already rendered, and the voice cast.

Rendering a full book takes hours, so an interrupted run must never start over.
We persist a small JSON alongside the chapter WAVs; a chapter counts as done
only when its WAV exists and is recorded here, so a crash mid-chapter simply
re-renders that one chapter.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field


@dataclass
class RenderState:
    source: str
    title: str
    narrator_voice: str
    cast: dict[str, str] = field(default_factory=dict)
    characters: list[str] = field(default_factory=list)
    completed: list[int] = field(default_factory=list)  # chapter indices
    chapter_titles: dict[str, str] = field(default_factory=dict)
    # chapter index -> hash of its source text, so edits invalidate stale audio.
    chapter_hashes: dict[str, str] = field(default_factory=dict)

    _path: str = field(default="", repr=False)

    @classmethod
    def load_or_new(cls, path: str, source: str, title: str, narrator_voice: str) -> "RenderState":
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            # A different source book in the same dir must not resume falsely.
            if data.get("source") == source:
                state = cls(**{k: v for k, v in data.items() if not k.startswith("_")})
                state._path = path
                return state
        state = cls(source=source, title=title, narrator_voice=narrator_voice)
        state._path = path
        return state

    def is_done(self, chapter_index: int, wav_path: str, text_hash: str) -> bool:
        return (
            chapter_index in self.completed
            and os.path.exists(wav_path)
            and self.chapter_hashes.get(str(chapter_index)) == text_hash
        )

    def mark_done(self, chapter_index: int, title: str, text_hash: str) -> None:
        if chapter_index not in self.completed:
            self.completed.append(chapter_index)
        self.chapter_titles[str(chapter_index)] = title
        self.chapter_hashes[str(chapter_index)] = text_hash
        self.save()

    def save(self) -> None:
        data = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self._path)
