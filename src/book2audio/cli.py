"""Command-line interface: `book2audio generate <epub>`.

One command turns an EPUB into a finished audiobook with sane defaults; every
knob from the PRD's user-controls section is an optional flag. `--dry-run`
parses and casts without synthesizing, so you can review chapters and detected
characters in seconds before committing to a multi-hour render.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from rich.console import Console

from book2audio.config import Config
from book2audio.pipeline import Pipeline


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    console = Console()

    if args.command == "generate":
        return _run_generate(args, console)
    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="book2audio", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")

    gen = sub.add_parser("generate", help="Generate an audiobook from an EPUB")
    gen.add_argument("epub", help="Path to the .epub file")
    gen.add_argument("-o", "--out", help="Output .m4b path (default: ./output/<name>.m4b)")
    gen.add_argument("--work-dir", help="Scratch dir for chapter audio + resume state")
    gen.add_argument("--narrator", default=None, help="Narrator voice id")
    gen.add_argument(
        "--voice",
        action="append",
        default=[],
        metavar="NAME=VOICE",
        help="Override a character's voice (repeatable), e.g. --voice Sunny=am_adam",
    )
    gen.add_argument("--speed", type=float, default=1.0, help="Speaking speed (0.5–2.0)")
    gen.add_argument("--bitrate", default="64k", help="AAC bitrate for the .m4b")
    gen.add_argument(
        "--limit", type=int, default=0, metavar="N", help="Only render the first N chapters"
    )
    gen.add_argument(
        "--batch",
        type=int,
        default=10,
        metavar="N",
        help="Emit a playable .m4b every N chapters for early listening (0 = single file)",
    )
    gen.add_argument(
        "--chapter-gap",
        type=float,
        default=1.8,
        metavar="SECONDS",
        help="Pause between chapters (default: 1.8s)",
    )
    gen.add_argument(
        "--no-music",
        action="store_false",
        dest="music",
        default=True,
        help="Disable the ambient background music bed",
    )
    gen.add_argument(
        "--music-volume",
        type=float,
        default=0.22,
        metavar="0-1",
        help="Background music loudness relative to narration (default: 0.22)",
    )
    gen.add_argument(
        "--no-expressive",
        action="store_false",
        dest="expressive",
        default=True,
        help="Disable punctuation-driven pacing/energy variation",
    )
    gen.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse, attribute, and cast without synthesizing audio",
    )
    return parser


def _run_generate(args: argparse.Namespace, console: Console) -> int:
    slug = _slugify(os.path.splitext(os.path.basename(args.epub))[0])
    out_path = args.out or os.path.join("output", f"{slug}.m4b")
    work_dir = args.work_dir or os.path.join("output", slug)
    os.makedirs(work_dir, exist_ok=True)

    from book2audio.tts.voices import DEFAULT_NARRATOR_VOICE

    config = Config(
        epub_path=args.epub,
        output_path=out_path,
        work_dir=work_dir,
        narrator_voice=args.narrator or DEFAULT_NARRATOR_VOICE,
        voice_overrides=_parse_overrides(args.voice),
        speed=args.speed,
        bitrate=args.bitrate,
        chapter_limit=args.limit,
        batch_size=args.batch,
        chapter_gap=args.chapter_gap,
        music=args.music,
        music_volume=args.music_volume,
        expressive=args.expressive,
    )

    try:
        pipeline = Pipeline(config, console)
        if args.dry_run:
            _dry_run(pipeline, console)
            return 0
        pipeline.run()
        return 0
    except (ValueError, RuntimeError) as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        return 1


def _dry_run(pipeline: Pipeline, console: Console) -> None:
    pipeline.config.validate()
    book = pipeline._load_and_prepare()
    cast = pipeline._cast_voices(book)
    selected = pipeline._select_chapters(book)
    pipeline._report_plan(book, selected, cast)
    console.print("\n[bold]Full cast:[/bold]")
    for name, voice in cast.as_dict().items():
        console.print(f"  {name:<24} {voice}")


def _parse_overrides(raw: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in raw:
        if "=" not in item:
            raise SystemExit(f"Invalid --voice {item!r}; expected NAME=VOICE")
        name, voice = item.split("=", 1)
        overrides[name.strip()] = voice.strip()
    return overrides


def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_-]+", "-", text) or "audiobook"


if __name__ == "__main__":
    sys.exit(main())
