"""Generate and upload a short demo clip for every curated voice.

Standalone CLI, never imported by the API or worker request path. Run this
once (or whenever the voice pool changes) to populate the samples the
`/voices` endpoint presigns for the frontend's voice picker.

Usage:
    uv run python scripts/generate_voice_samples.py
"""

from __future__ import annotations

import os
import tempfile

from book2audio.assemble.audio import write_wav
from book2audio.storage.r2 import upload_bytes
from book2audio.tts.engine import KokoroEngine
from book2audio.tts.voices import CHARACTER_VOICE_POOL, DEFAULT_NARRATOR_VOICE

DEMO_TEXT = (
    "The old bridge creaked under the weight of the storm, and somewhere in "
    "the dark, footsteps began to follow."
)


def main() -> int:
    voice_ids = (DEFAULT_NARRATOR_VOICE, *CHARACTER_VOICE_POOL)
    engine = KokoroEngine()

    for voice_id in voice_ids:
        print(f"Generating sample for {voice_id}...")
        samples = engine.synthesize(DEMO_TEXT, voice=voice_id, speed=1.0)

        with tempfile.TemporaryDirectory() as tmp_dir:
            wav_path = os.path.join(tmp_dir, f"{voice_id}.wav")
            write_wav(wav_path, samples)
            with open(wav_path, "rb") as f:
                data = f.read()

        key = f"voice-samples/{voice_id}.wav"
        upload_bytes(data, key, content_type="audio/wav")
        print(f"  uploaded {key} ({len(data)} bytes)")

    print(f"Done. Generated {len(voice_ids)} voice samples.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
