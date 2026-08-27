"""Assign a stable Kokoro voice to the narrator and to every character.

Consistency is a hard requirement (PRD): a given character must keep the same
voice for the entire book. We get that for free by hashing the character name
into a fixed pool of distinct voices — deterministic, order-independent, and
survives resume. Users can override any assignment via ``overrides``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from book2audio.models import NARRATOR

# Warm, clear male narrator — distinct from the character pool below.
DEFAULT_NARRATOR_VOICE = "am_michael"

# A spread of Kokoro v1.0 voices (mixed gender/accent) used to voice characters.
# The narrator voice is intentionally excluded so characters never collide with
# the narrator. Order is fixed so hashing is reproducible across runs.
CHARACTER_VOICE_POOL = (
    "af_heart",
    "am_adam",
    "bf_emma",
    "bm_lewis",
    "af_sarah",
    "am_eric",
    "bf_isabella",
    "am_liam",
    "af_nicole",
    "bm_george",
    "af_sky",
    "am_onyx",
    "af_bella",
    "am_puck",
)

# Kokoro encodes gender in the voice-id prefix: *f_* female, *m_* male. We split
# the pool so a character guessed male gets a male voice, and vice versa.
MALE_VOICES = tuple(v for v in CHARACTER_VOICE_POOL if v[1] == "m")
FEMALE_VOICES = tuple(v for v in CHARACTER_VOICE_POOL if v[1] == "f")


@dataclass
class VoiceCast:
    """Resolved narrator + character -> voice assignments for one book."""

    narrator_voice: str = DEFAULT_NARRATOR_VOICE
    overrides: dict[str, str] = field(default_factory=dict)
    _assignments: dict[str, str] = field(default_factory=dict)

    def cast(self, characters: list[str], genders: dict[str, str] | None = None) -> None:
        """Assign pool voices to characters in the given order (idempotent).

        Callers pass characters most-frequent-speaker first; assigning
        sequentially guarantees the busiest characters get *distinct* voices (no
        collisions between the two leads who trade lines every page). When a
        ``genders`` guess is available, each character draws from the matching
        gender pool first; otherwise from the full pool. Voices only repeat once
        a pool is exhausted.
        """
        genders = genders or {}
        used = set(self._assignments.values()) | set(self.overrides.values())
        cursors = {"m": 0, "f": 0, "any": 0}
        pools = {
            "m": self._rotate(MALE_VOICES, used),
            "f": self._rotate(FEMALE_VOICES, used),
            "any": self._rotate(CHARACTER_VOICE_POOL, used),
        }
        for name in characters:
            if name in self._assignments or name in self.overrides:
                continue
            key = genders.get(name, "any")
            pool = pools[key] or pools["any"]
            self._assignments[name] = pool[cursors[key] % len(pool)]
            cursors[key] += 1

    @staticmethod
    def _rotate(pool: tuple[str, ...], used: set[str]) -> list[str]:
        """Voices not yet used first, then the rest, so distinct wins early."""
        return [v for v in pool if v not in used] + [v for v in pool if v in used]

    def voice_for(self, speaker: str) -> str:
        """Return the voice id for a speaker, casting on demand if needed."""
        if speaker == NARRATOR:
            return self.narrator_voice
        if speaker in self.overrides:
            return self.overrides[speaker]
        if speaker not in self._assignments:
            self._assignments[speaker] = self._pick_voice(speaker)
        return self._assignments[speaker]

    def as_dict(self) -> dict[str, str]:
        """Full narrator + character mapping, for display and the resume state."""
        return {NARRATOR: self.narrator_voice, **self._assignments, **self.overrides}

    def _pick_voice(self, name: str) -> str:
        if name in self.overrides:
            return self.overrides[name]
        digest = hashlib.sha1(name.encode("utf-8")).digest()
        return CHARACTER_VOICE_POOL[digest[0] % len(CHARACTER_VOICE_POOL)]
