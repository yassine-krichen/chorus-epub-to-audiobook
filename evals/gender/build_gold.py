"""Turn the local gender gold (with verbatim quotes) into the commit-safe gold files.

    python evals/gender/build_gold.py

Reads ``local/gold-<slice>.json`` (gitignored: it quotes the book) and writes
``gold/<slice>.json``: character names, aliases, gender and, in place of each
quote, the chapter plus a short SHA-256 of the quote. The hash lets a reader with
the book check the evidence without the repository carrying any of its text.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

import common

_NOTE_IN_PARENTHESES = re.compile(r"\s*\(.*\)\s*$")


def quote_hash(quote: str) -> str:
    normalized = " ".join(quote.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def commit_safe(character: dict) -> dict:
    aliases = []
    for alias in character.get("aliases", []):
        cleaned = _NOTE_IN_PARENTHESES.sub("", alias).strip().strip('"')
        if cleaned and cleaned not in aliases:
            aliases.append(cleaned)
    return {
        "name": character["name"],
        "aliases": aliases,
        "gender": character["gold_gender"],
        "approx_dialogue_lines": character.get("approx_dialogue_lines", 0),
        "evidence": [
            {"chapter": item["chapter"], "quote_sha256_12": quote_hash(item["quote"])}
            for item in character.get("evidence", [])
        ],
    }


def main() -> int:
    for slice_name, spec in common.SLICES.items():
        source = os.path.join(common.LOCAL_DIR, f"gold-{slice_name}.json")
        with open(source, encoding="utf-8") as fh:
            characters = json.load(fh)
        payload = {
            "slice": slice_name,
            "book": common.BOOK_LABEL,
            "first_chapter": spec.first,
            "last_chapter": spec.last,
            "characters": [commit_safe(c) for c in characters],
        }
        target = os.path.join(common.GOLD_DIR, f"{slice_name}.json")
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print(f"wrote {target} ({len(characters)} characters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
