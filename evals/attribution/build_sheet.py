"""Download the evaluation books, build blank labelling sheets, ingest labels.

    python evals/attribution/build_sheet.py download
    python evals/attribution/build_sheet.py sheets
    python evals/attribution/build_sheet.py gold --split test

Sheets deliberately carry no heuristic or LLM prediction: the person labelling
must not be anchored by the system under test.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.request

import common
import scoring

SHEET_COLUMNS = ["id", "context_before", "line", "context_after", "speaker", "note"]
CONTEXT_SEGMENTS = 2
CONTEXT_JOINER = " ¶ "
CAST_NOTE_PREFIX = "CAST:"

# Seed for the gold file's alias table: every spelling a prediction or a label
# might use for the same character. Edit the gold file's copy after ingesting.
SEED_ALIASES: dict[str, dict[str, list[str]]] = {
    "alice": {
        "Alice": [],
        "Mad Hatter": ["Hatter", "The Hatter", "The Mad Hatter"],
        "March Hare": ["Hare", "The March Hare"],
        "Dormouse": ["The Dormouse"],
        "Caterpillar": ["The Caterpillar"],
        "Pigeon": [],
        "Duchess": ["The Duchess"],
        "Queen": ["Queen of Hearts", "The Queen"],
        "Gryphon": ["The Gryphon", "Griffin"],
        "Mock Turtle": ["The Mock Turtle", "Turtle"],
        "Cheshire Cat": ["Cat", "The Cat", "Cheshire-Cat"],
        "Footman": ["The Footman", "Frog Footman", "Frog"],
        "Cook": ["The Cook"],
    },
    "penrod": {
        "Penrod": ["Penrod Schofield"],
        "Sam Williams": ["Sam", "Samuel Williams"],
        "Herman": [],
        "Verman": [],
        "Roddy": ["Roderick", "Roderick Magsworth Bitts", "Master Bitts"],
        "Mrs. Bitts": ["Mrs. Roderick Magsworth Bitts"],
        "Mrs. Williams": [],
        "Mr. Williams": [],
        "Mrs. Schofield": [],
        "Mr. Schofield": [],
        "Margaret": ["Margaret Schofield", "Miss Margaret Schofield"],
        "Robert Williams": ["Bob", "Mr. Robert Williams", "Robert"],
        "Marjorie": ["Marjorie Jones"],
        "Mitchy-Mitch": ["Mitchy", "Mitchell", "Mitch"],
    },
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    download = sub.add_parser("download", help="Fetch the EPUBs into data/ and pin their sha256")
    download.add_argument("--repin", action="store_true", help="Accept a changed sha256")

    sub.add_parser("sheets", help="Write blank labelling sheets for the test and dev splits")

    gold = sub.add_parser("gold", help="Turn a labelled sheet into a gold file")
    gold.add_argument("--split", required=True, choices=["test", "dev"])
    gold.add_argument("--sheet", help="Labelled CSV (default: sheets/<split>.csv)")
    gold.add_argument("--out", help="Gold JSON (default: gold/<split>.json)")

    args = parser.parse_args(argv)
    try:
        if args.command == "download":
            return _download(args.repin)
        if args.command == "sheets":
            return _sheets()
        return _gold(args.split, args.sheet, args.out)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


# --- download ---------------------------------------------------------------


def _download(repin: bool) -> int:
    manifest = common.load_books_manifest()
    os.makedirs(common.DATA_DIR, exist_ok=True)
    for slug, book in manifest["books"].items():
        path = common.epub_path(book)
        if not os.path.isfile(path):
            print(f"downloading {slug}: {book['url']}")
            request = urllib.request.Request(
                book["url"], headers={"User-Agent": "book2audio-attribution-eval/1.0"}
            )
            # Write beside the target and rename, so an interrupted download can
            # never leave a truncated file that later passes as "already there".
            part_path = path + ".part"
            with urllib.request.urlopen(request, timeout=60) as response, open(
                part_path, "wb"
            ) as out:
                out.write(response.read())
            os.replace(part_path, path)
        digest = scoring.sha256_file(path)
        pinned = book.get("sha256")
        if pinned and pinned != digest and not repin:
            raise ValueError(
                f"{slug}: downloaded sha256 {digest[:12]}... != pinned {pinned[:12]}.... "
                "Gutenberg regenerated the file; labels made on the old one may not line up. "
                "Pass --repin to accept it and rebuild the sheets."
            )
        book["sha256"] = digest
        print(f"{slug}: {path} sha256={digest}")
    common.save_books_manifest(manifest)
    return 0


def _pinned_chapters(slug: str, book: dict) -> list:
    path = common.epub_path(book)
    if not os.path.isfile(path):
        raise ValueError(f"{path} is missing; run the download command first")
    scoring.verify_epub_hash(book.get("sha256", ""), scoring.sha256_file(path), slug)
    return common.load_chapters(path)


# --- sheets -------------------------------------------------------------------


def _render_segment(seg) -> str:
    if seg.kind.value == "dialogue":
        return f"“{seg.text}”"
    if seg.kind.value == "scene_break":
        return "***"
    return seg.text


def _context(chapter, start: int, stop: int) -> str:
    start, stop = max(0, start), min(len(chapter.segments), stop)
    return CONTEXT_JOINER.join(_render_segment(s) for s in chapter.segments[start:stop])


def _sheets() -> int:
    manifest = common.load_books_manifest()
    os.makedirs(common.SHEETS_DIR, exist_ok=True)
    chapters_by_book = {
        slug: _pinned_chapters(slug, book) for slug, book in manifest["books"].items()
    }
    for split, selection in manifest["splits"].items():
        rows: list[dict[str, str]] = []
        cast_lines: list[str] = []
        for slug, chapter_indices in selection.items():
            lines = common.dialogue_lines(slug, chapters_by_book[slug])
            for chapter_index in chapter_indices:
                chapter_lines = [d for d in lines if d.chapter.index == chapter_index]
                if not chapter_lines:
                    raise ValueError(f"{slug} has no chapter {chapter_index} with dialogue")
                title = chapter_lines[0].chapter.title
                cast = manifest["books"][slug]["cast"][str(chapter_index)]
                cast_text = "; ".join(cast)
                cast_lines.append(f"{slug} chapter {chapter_index} ({title}): {cast_text}")
                for n, d in enumerate(chapter_lines):
                    chapter = d.chapter
                    rows.append(
                        {
                            "id": d.id,
                            "context_before": _context(
                                chapter, d.position - CONTEXT_SEGMENTS, d.position
                            ),
                            "line": chapter.segments[d.position].text,
                            "context_after": _context(
                                chapter, d.position + 1, d.position + 1 + CONTEXT_SEGMENTS
                            ),
                            "speaker": "",
                            "note": f"{CAST_NOTE_PREFIX} {cast_text}" if n == 0 else "",
                        }
                    )
        sheet_path = os.path.join(common.SHEETS_DIR, f"{split}.csv")
        # utf-8-sig so Excel opens the curly quotes correctly.
        with open(sheet_path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=SHEET_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        cast_path = os.path.join(common.SHEETS_DIR, f"{split}.characters.txt")
        with open(cast_path, "w", encoding="utf-8") as fh:
            fh.write(
                "Characters per chapter (hand-written from the text, not from any model).\n"
                "Special speaker values: Narrator (quoted words no character says aloud), "
                "SKIP (cannot be decided; excluded from scoring).\n\n"
            )
            fh.write("\n".join(cast_lines) + "\n")
        print(f"{split}: {len(rows)} line(s) -> {sheet_path}")
    return 0


# --- gold -----------------------------------------------------------------------


def _gold(split: str, sheet: str | None, out: str | None) -> int:
    manifest = common.load_books_manifest()
    sheet_path = sheet or os.path.join(common.SHEETS_DIR, f"{split}.csv")
    out_path = out or os.path.join(common.GOLD_DIR, f"{split}.json")

    with open(sheet_path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != SHEET_COLUMNS:
            raise ValueError(f"{sheet_path}: expected columns {SHEET_COLUMNS}")
        rows = list(reader)

    ids = [r["id"] for r in rows]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(
            f"{len(duplicates)} duplicate id(s) in the sheet, e.g. {duplicates[0]}. "
            "Each line must appear once."
        )

    unlabeled = [r["id"] for r in rows if not r["speaker"].strip()]
    if unlabeled:
        raise ValueError(
            f"{len(unlabeled)} of {len(rows)} line(s) have no speaker yet, e.g. {unlabeled[0]}. "
            "Label every line (use SKIP when undecidable)."
        )

    book_of_id: dict[str, str] = {}
    for slug, book in manifest["books"].items():
        for d in common.dialogue_lines(slug, _pinned_chapters(slug, book)):
            book_of_id[d.id] = slug
    unknown = [r["id"] for r in rows if r["id"] not in book_of_id]
    if unknown:
        raise ValueError(f"{len(unknown)} sheet id(s) not in the pinned books, e.g. {unknown[0]}")

    used_books = sorted({book_of_id[r["id"]] for r in rows})
    gold_lines = [
        scoring.GoldLine(
            id=r["id"],
            book=book_of_id[r["id"]],
            speaker=r["speaker"].strip(),
            note="" if r["note"].startswith(CAST_NOTE_PREFIX) else r["note"].strip(),
        )
        for r in rows
    ]
    aliases = _existing_aliases(out_path) or {
        name: variants for slug in used_books for name, variants in SEED_ALIASES.get(slug, {}).items()
    }
    _check_speakers(rows, book_of_id, manifest, aliases)
    gold = scoring.Gold(
        split=split,
        books={slug: {"epub_sha256": manifest["books"][slug]["sha256"]} for slug in used_books},
        aliases=aliases,
        lines=gold_lines,
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(gold.to_json(), fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    skipped = sum(1 for g in gold_lines if g.speaker.casefold() == scoring.SKIP_LABEL.casefold())
    print(f"{split}: {len(gold_lines)} line(s), {skipped} SKIP -> {out_path}")
    return 0


def _check_speakers(rows: list[dict], book_of_id: dict, manifest: dict, aliases: dict) -> None:
    """Every speaker must be a chapter cast member, Narrator, SKIP, or a known alias.

    Catches typos ("Alise") that would otherwise score as permanent errors.
    """
    alias_map = scoring.build_alias_map(aliases)
    special = {scoring.normalize_name(v) for v in (scoring.NARRATOR_LABEL, scoring.SKIP_LABEL)}
    bad: list[str] = []
    for r in rows:
        chapter = r["id"].split(":", 1)[0]
        cast = manifest["books"][book_of_id[r["id"]]]["cast"].get(chapter, [])
        allowed = {scoring.canonicalize(name, alias_map) for name in cast} | special
        allowed |= set(alias_map)  # any name or spelling in the alias table
        if scoring.canonicalize(r["speaker"], alias_map) not in allowed:
            bad.append(f"{r['id']} -> {r['speaker'].strip()!r}")
    if bad:
        shown = "; ".join(bad[:10])
        raise ValueError(
            f"{len(bad)} row(s) name a speaker that is not in the chapter's cast list "
            f"(books.json) or the alias table: {shown}"
            f"{' ...' if len(bad) > 10 else ''}. Fix the typo, or add the character to "
            "the cast list or the alias table."
        )


def _existing_aliases(path: str) -> dict[str, list[str]] | None:
    """Keep hand-edited aliases when the gold file is regenerated."""
    if not os.path.isfile(path):
        return None
    return scoring.load_gold(path).aliases or None


if __name__ == "__main__":
    sys.exit(main())
