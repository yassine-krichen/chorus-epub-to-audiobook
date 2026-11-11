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
from rich.markup import escape
from rich.table import Table

from book2audio.assemble.music import MUSIC_PRESETS
from book2audio.attribution import ATTRIBUTOR_MODES, require_llm_attribution_configured
from book2audio.config import Config
from book2audio.images.freellmapi import require_freellmapi_configured
from book2audio.models import Book
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
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Only render N chapters, counted in the book's chapter numbers from the start "
            "(--limit 10 renders Chapters 1 to 10; a prologue before Chapter 1 comes extra)"
        ),
    )
    gen.add_argument(
        "--start-chapter",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Book chapter number to start from. Default: automatic, the story's first chapter "
            "(synopsis and info pages are skipped). 0 includes them. Use --dry-run to see the "
            "numbers"
        ),
    )
    gen.add_argument(
        "--batch",
        type=int,
        default=10,
        metavar="N",
        help=(
            "Emit a playable .m4b every N chapters for early listening (0 = single file). "
            "--batch 1 writes one file per chapter, named like '001 - Nightmare Begins.m4b'"
        ),
    )
    gen.add_argument(
        "--filename-template",
        default=None,
        metavar="TEMPLATE",
        help=(
            "Name output files from a template, placed next to --out. Placeholders: {title} "
            "{part} {part:0Nd} {chapter} {chapter:0Nd} (book number of the file's first chapter) "
            "and {chapter_title} (only with --batch 1). Default with --batch 1: "
            "'{chapter:03d} - {chapter_title}'; otherwise --out and <out>-part-NN are used"
        ),
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
    gen.add_argument(
        "--male-voice", default=None, help="Preferred voice for male-guessed characters"
    )
    gen.add_argument(
        "--female-voice", default=None, help="Preferred voice for female-guessed characters"
    )
    gen.add_argument(
        "--music-preset",
        default="ambient",
        help=f"Background music style (default: ambient). Known: {', '.join(sorted(MUSIC_PRESETS))}",
    )
    gen.add_argument(
        "--thumbnails",
        action="store_true",
        default=False,
        help=(
            "Enable per-chapter cover thumbnails. Validates freellmapi credentials but "
            "does not yet generate local thumbnail files — that machinery currently only "
            "exists via the hosted worker's R2-backed job pipeline."
        ),
    )
    gen.add_argument(
        "--attributor",
        choices=ATTRIBUTOR_MODES,
        default="heuristic",
        help=(
            "Who speaks each line: heuristic (offline, default), llm (ask the model about "
            "every line) or hybrid (ask only about lines the heuristic had to guess). "
            "llm/hybrid need FREELLMAPI_BASE_URL and FREELLMAPI_API_KEY."
        ),
    )
    gen.add_argument(
        "--attribution-max-calls",
        type=int,
        default=150,
        metavar="N",
        help=(
            "Max LLM windows (one request each) per book; later windows keep heuristic "
            "labels. A transient failure may be retried once, outside this count (default: 150)"
        ),
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
        start_chapter=args.start_chapter,
        batch_size=args.batch,
        filename_template=args.filename_template,
        chapter_gap=args.chapter_gap,
        music=args.music,
        music_volume=args.music_volume,
        music_preset=args.music_preset,
        expressive=args.expressive,
        male_voice=args.male_voice,
        female_voice=args.female_voice,
        thumbnails=args.thumbnails,
        attributor=args.attributor,
        attribution_max_calls=args.attribution_max_calls,
    )

    try:
        if config.thumbnails:
            require_freellmapi_configured()
        if config.attributor != "heuristic":
            require_llm_attribution_configured()
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
    book = pipeline._load_and_prepare(estimate_only=True)
    cast = pipeline._cast_voices(book)
    # Printed before selecting so a start_chapter that matches nothing still
    # shows the numbers the book does have.
    _print_chapter_table(book, pipeline.config, console)
    selected = pipeline._select_chapters(book)
    pipeline._report_plan(book, selected, cast)
    _print_planned_files(pipeline.planned_file_names(selected, book.metadata), console)
    console.print("\n[bold]Full cast:[/bold]")
    for name, voice in cast.as_dict().items():
        console.print(f"  {name:<24} {voice}")


def _print_planned_files(names: list[str], console: Console) -> None:
    if not names:
        return
    shown = names[:12]
    console.print("\n[bold]Output files:[/bold]")
    for name in shown:
        console.print(f"  {escape(name)}")
    if len(names) > len(shown):
        console.print(f"[dim]  ... and {len(names) - len(shown)} more[/dim]")


def _print_chapter_table(book: Book, config: Config, console: Console) -> None:
    """Show how the book was numbered and which chapters this run would render."""
    start_pos, end_pos = config.selection_bounds(book.chapters)
    table = Table(title="Chapters")
    table.add_column("Number", justify="right")
    table.add_column("Title")
    table.add_column("Words", justify="right")
    table.add_column("Front matter")
    table.add_column("Selected")
    # Rows after the selection are summarised below the table: a 100-chapter
    # book would otherwise bury the part the user is checking.
    shown = book.chapters[: max(end_pos, start_pos)]
    for position, chapter in enumerate(shown):
        selected = start_pos <= position < end_pos
        table.add_row(
            str(chapter.number),
            escape(chapter.title),
            f"{chapter.word_count:,}",
            "yes" if chapter.front_matter else "",
            "yes" if selected else "",
        )
    console.print(table)
    remaining = len(book.chapters) - len(shown)
    if remaining:
        console.print(f"[dim]... and {remaining} more section(s) after the selection[/dim]")


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
