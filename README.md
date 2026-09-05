# book2audio

Turn an EPUB into a natural, **multi-voice audiobook** (`.m4b` with chapters,
cover art, and metadata) with a single command — fully local, no API keys, no
per-minute billing.

```bash
book2audio generate "mybook.epub"
```

The narrator gets one voice; each detected character gets their own, kept
consistent for the whole book. Dialogue and narration are separated
automatically — no manual speaker tagging.

---

## Why

Commercial AI audiobook tools are cloud-only, priced per minute, and send your
library to someone else's servers. `book2audio` runs entirely on your machine
using the [Kokoro](https://github.com/thewh1teagle/kokoro-onnx) neural TTS model
(ONNX — no PyTorch, no GPU required) and `ffmpeg`. A ~140k-word novel renders to
a finished, chapterized audiobook for the cost of some electricity.

---

## Requirements

- **Python 3.12** (managed automatically if you use [`uv`](https://docs.astral.sh/uv/))
- **ffmpeg** on your `PATH` (for AAC/M4B muxing and chapter markers)

The Kokoro model files (~325 MB) download automatically on first run and are
cached under `%LOCALAPPDATA%\book2audio\models` (or `~/.cache` on Unix).

---

## Quickstart

```bash
# install deps into an isolated env
uv sync

# preview: chapters, detected characters, and the voice cast — no audio (seconds)
uv run book2audio generate "epubs/mybook.epub" --dry-run

# render the whole book to output/mybook.m4b
uv run book2audio generate "epubs/mybook.epub"

# render just the first 3 chapters (fast sanity check)
uv run book2audio generate "epubs/mybook.epub" --limit 3
```

Rendering is **resumable**: interrupt it any time and re-run the same command —
completed chapters are skipped, and only the chapter that was mid-render repeats.

---

## Usage

```
book2audio generate <epub> [options]

  -o, --out PATH        Output .m4b path (default: ./output/<name>.m4b)
      --work-dir PATH   Scratch dir for chapter audio + resume state
      --narrator VOICE  Narrator voice id (default: am_michael)
      --voice NAME=VOICE  Override a character's voice (repeatable)
      --male-voice VOICE    Preferred voice for male-guessed characters
      --female-voice VOICE  Preferred voice for female-guessed characters
      --speed FLOAT     Speaking speed 0.5–2.0 (default: 1.0)
      --bitrate STR     AAC bitrate (default: 64k)
      --limit N         Only render the first N chapters
      --batch N         Emit a playable .m4b every N chapters (default 10; 0 = one file)
      --chapter-gap SECONDS  Pause between chapters (default: 1.8)
      --no-music        Disable the ambient background music bed
      --music-volume 0-1  Background music loudness relative to narration (default: 0.22)
      --music-preset NAME    Background music style: ambient, drone, rain (default: ambient)
      --no-expressive   Disable punctuation-driven pacing/energy variation
      --dry-run         Parse, attribute, and cast without synthesizing
      --thumbnails      Enable per-chapter cover thumbnails (validates freellmapi
                         credentials only; actual thumbnail files are currently
                         produced by the hosted worker, not this CLI)
```

### Audio production

Three things shape how "produced" the result feels, all on by default:

- **Chapter pause** — a 1.8s beat of silence between chapters (`--chapter-gap`),
  so a chapter change is audibly clear instead of running straight into the next.
- **Ambient music bed** — a warm, procedurally generated pad (no external audio
  files — synthesized detuned sine partials over a low chord, with slow
  amplitude movement and a touch of filtered noise for atmosphere) mixed
  under the narration, swelling in at the start of a chapter and fading out
  through the chapter-gap pause. Loudness is set *relative* to the narration's
  own volume (`--music-volume`, default 0.22 = 22% of speech RMS). `--no-music`
  turns it off entirely.
- **Loudness normalization + pacing** — every synthesized line is normalized to
  a consistent volume (raw TTS output varies clip to clip, which reads as
  "flat/uneven" as much as monotone), then nudged by its punctuation: a line
  ending in "!" is read slightly faster and louder, one trailing off in "…"
  slightly slower and softer. `--no-expressive` disables the pacing nudge (the
  loudness normalization always applies).

**Ceiling, honestly:** Kokoro takes plain text — there's no SSML, no way to
mark one specific *word* for emphasis. The above measurably reduces the flat,
sample-to-sample sameness that reads as robotic; it doesn't produce real
word-level emphasis. That needs a different TTS backend, which is a separate
cost/local-vs-cloud decision, not implemented here.

### Progressive output

By default the book is delivered **part by part**: a playable `.m4b` is written
every 10 chapters (`<name>-part-01.m4b`, `-part-02.m4b`, …) the moment that
batch finishes, so you can start listening while the rest still renders. Each
part carries its own chapter list and a title like *"… — Part 2 (Ch 11-20)"*.
Use `--batch 0` for a single file, or a smaller `--batch` to start listening
sooner.

Example with overrides:

```bash
book2audio generate book.epub \
  --narrator bm_george \
  --voice Sunny=am_adam \
  --voice Nephis=bf_emma \
  --speed 1.1
```

Run `--dry-run` first to see the detected character names, then pin any voice
you want with `--voice NAME=VOICE`.

---

## How it works

A straight pipeline, each stage pure and independently testable:

```
EPUB ─▶ parse ─▶ segment ─▶ attribute ─▶ cast ─▶ synthesize ─▶ assemble ─▶ .m4b
       (epub/)  (text/)   (attribution/) (tts/)   (tts/)      (assemble/)
```

1. **Parse** (`epub/`) — read the spine for reading order, pull each chapter's
   heading and paragraphs, extract the cover, and skip front matter.
2. **Segment** (`text/`) — split every paragraph into ordered narration and
   dialogue runs (`"…"`), and mark scene breaks (`***`).
3. **Attribute** (`attribution/`) — decide who speaks each line using dialogue
   tags ("… said Sunny"), pronoun resolution, and two-speaker alternation.
   Nicknames fold into full names; character gender is guessed from tag pronouns.
   The `Attributor` interface is swappable — an LLM strategy can drop in later.
4. **Cast** (`tts/voices.py`) — assign a stable voice per character, busiest
   speakers first so the leads never share a voice; gender-matched when known.
5. **Synthesize** (`tts/engine.py`) — Kokoro renders each segment; long text is
   chunked on sentence boundaries for clean prosody.
6. **Assemble** (`assemble/`) — join segments with natural pauses, normalize
   loudness, mix in the ambient music bed, then `ffmpeg` muxes chapter WAVs
   into `.m4b` file(s) with chapter markers, cover, and tags.

---

## Testing

```bash
uv run pytest
```

Tests cover the deterministic core — segmentation, speaker attribution, voice
casting, text normalization, and EPUB parsing. TTS/ffmpeg are exercised via the
`--limit` render path rather than mocked.

---

## Roadmap

This is the automated core of a larger vision (see `DESC.md`). Designed-for-but-
not-yet-built:

- **LLM attribution & casting** — swap the heuristic `Attributor` for an
  LLM that labels speakers and casts by personality/age/role.
- **True word-level emphasis** — needs a TTS backend with SSML/prosody control;
  a cost/local-vs-cloud decision, not a quick swap.
- **AI-generated chapter images** — deferred pending a local-vs-cloud decision
  (GPU/latency/cost/privacy tradeoffs).
- **Library, player, and GUI** — browse, review characters, preview voices,
  regenerate sections.

---

## Voices

Kokoro voice ids follow `<accent><gender>_<name>` — e.g. `am_michael` (American
male), `bf_emma` (British female). The character pool and default narrator live
in `src/book2audio/tts/voices.py`.

