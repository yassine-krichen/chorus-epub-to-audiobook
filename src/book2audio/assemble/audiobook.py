"""Mux per-chapter WAVs into a single .m4b with chapters, cover, and metadata.

We shell out to ffmpeg (already a hard requirement for M4B/AAC): it concatenates
the chapter WAVs, embeds an ffmetadata file carrying chapter markers and tags,
and attaches the cover as album art. The result opens as a proper audiobook in
any player, with a navigable chapter list and resume support.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from book2audio.assemble.audio import duration_seconds
from book2audio.models import BookMetadata


@dataclass
class ChapterAudio:
    title: str
    wav_path: str


def build_m4b(
    chapters: list[ChapterAudio],
    metadata: BookMetadata,
    out_path: str,
    work_dir: str,
    bitrate: str = "64k",
) -> str:
    """Assemble ``chapters`` into ``out_path`` (.m4b) and return the path."""
    concat_list = _write_concat_list(chapters, work_dir)
    ffmeta = _write_ffmetadata(chapters, metadata, work_dir)

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list, "-i", ffmeta]
    has_cover = bool(metadata.cover_path and os.path.exists(metadata.cover_path))
    if has_cover:
        cmd += ["-i", metadata.cover_path]

    cmd += ["-map", "0:a", "-map_metadata", "1"]
    if has_cover:
        cmd += ["-map", "2:v", "-c:v", "mjpeg", "-disposition:v", "attached_pic"]
    cmd += ["-c:a", "aac", "-b:a", bitrate, "-movflags", "+faststart", "-f", "mp4", out_path]

    _run(cmd)
    return out_path


def _write_concat_list(chapters: list[ChapterAudio], work_dir: str) -> str:
    path = os.path.join(work_dir, "concat.txt")
    with open(path, "w", encoding="utf-8") as f:
        for ch in chapters:
            # The concat demuxer resolves relative paths against the list file's
            # own directory, so use absolute paths. Forward slashes work on
            # every platform and avoid backslash-escaping surprises.
            abs_path = os.path.abspath(ch.wav_path).replace("\\", "/")
            safe = abs_path.replace("'", r"'\''")
            f.write(f"file '{safe}'\n")
    return path


def _write_ffmetadata(
    chapters: list[ChapterAudio], metadata: BookMetadata, work_dir: str
) -> str:
    path = os.path.join(work_dir, "ffmeta.txt")
    lines = [
        ";FFMETADATA1",
        f"title={_esc(metadata.title)}",
        f"artist={_esc(metadata.author)}",
        f"album={_esc(metadata.title)}",
        f"genre=Audiobook",
        f"language={_esc(metadata.language)}",
    ]
    start_ms = 0
    for ch in chapters:
        dur_ms = int(round(duration_seconds(ch.wav_path) * 1000))
        end_ms = start_ms + dur_ms
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start_ms}",
            # End one ms early so adjacent chapters don't overlap by a frame.
            f"END={max(start_ms, end_ms - 1)}",
            f"title={_esc(ch.title)}",
        ]
        start_ms = end_ms
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _esc(value: str) -> str:
    # ffmetadata treats = ; # \ and newlines as special; escape with backslash.
    for ch in ("\\", "=", ";", "#"):
        value = value.replace(ch, "\\" + ch)
    return value.replace("\n", " ")


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed while building the audiobook:\n" + result.stderr[-2000:]
        )
