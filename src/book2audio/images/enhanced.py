"""Enhanced thumbnails (level 2): the template prompt plus a per-chapter scene brief.

One chat call per chapter turns the chapter text into a brief (who is in the key
scene, what they look like, setting, mood, action). The brief is merged into the
level-1 template, and the main character's look is kept in an appearance sheet so
later chapters stay consistent. Everything here is best-effort: any failure while
getting a brief yields the plain template prompt, never a failed chapter.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Mapping, Sequence

from book2audio.attribution.gender import GenderVerdict
from book2audio.attribution.llm_client import LLMClient, require_llm_attribution_configured
from book2audio.images.appearance_sheet import (
    SHEET_FILENAME,
    AppearanceSheet,
    read_json_object,
    write_json_atomically,
)
from book2audio.images.scene_brief import (
    MAX_CAST,
    CastMember,
    SceneBrief,
    SceneBriefError,
    brief_cache_key,
    chapter_full_text,
    describe_scene,
    parse_scene_brief,
    request_scene_brief,
)
from book2audio.images.thumbnails import TEMPLATE_VERSION, build_prompt, chapter_content_hash
from book2audio.models import Chapter

logger = logging.getLogger(__name__)

BRIEF_CACHE_FILENAME = "scene_briefs.json"
LEVEL = "enhanced"
# A brief that cannot be had (rate limit, outage, bad replies) is retried for the
# next chapter; after this many in a row the rest of the job stays at level 1
# instead of waiting out a timeout on every chapter.
MAX_CONSECUTIVE_FAILURES = 3
# Shorter than the attribution default (120 s): a brief is a nicety, not worth a
# minute of waiting on a stuck free-tier model.
BRIEF_TIMEOUT_SECONDS = 45.0
# Files a resumed hosted job needs back from storage, besides state.json.
RESUME_FILENAMES = (SHEET_FILENAME, BRIEF_CACHE_FILENAME)


class BriefCache:
    """Scene briefs by input hash, so a re-run does not call the model again."""

    def __init__(self, path: str | None) -> None:
        self.path = path
        self._entries: dict[str, dict] = {}
        self.write_error: str | None = None
        loaded = read_json_object(path) if path else None
        entries = loaded.get("entries") if loaded else None
        if isinstance(entries, dict):
            self._entries = {k: v for k, v in entries.items() if isinstance(v, dict)}

    def get(self, key: str) -> dict | None:
        return self._entries.get(key)

    def put(self, key: str, brief: dict) -> None:
        self._entries[key] = brief
        if self.path:
            self.write_error = write_json_atomically(
                self.path, {"version": 1, "entries": self._entries}
            )


class EnhancedThumbnails:
    def __init__(self, client: LLMClient, work_dir: str, book_title: str) -> None:
        self._client = client
        self._book_title = book_title
        self._sheet_path = os.path.join(work_dir, SHEET_FILENAME)
        self._briefs = BriefCache(os.path.join(work_dir, BRIEF_CACHE_FILENAME))
        self._cast: list[CastMember] = []
        self._sheet = AppearanceSheet(None, None)
        self._consecutive_failures = 0
        self._disabled = False

    def set_cast(self, characters: Sequence[str], verdicts: Mapping[str, GenderVerdict]) -> None:
        """Receive the book's cast, most frequent speaker first (the Pipeline hook).

        The first one is the main character, whose look the sheet tracks. Only the
        top ``MAX_CAST`` speakers are kept: they are what the model is shown.
        """
        self._cast = [
            CastMember(name, verdicts[name].gender if name in verdicts else None)
            for name in characters[:MAX_CAST]
        ]
        main = self._cast[0].name if self._cast else None
        self._sheet = AppearanceSheet(self._sheet_path, main)

    @property
    def sheet(self) -> AppearanceSheet:
        return self._sheet

    def content_hash(self, chapter: Chapter) -> str:
        """Fingerprint of everything that decides this chapter's picture.

        Computed from the brief's *inputs* (not its reply), so it is known before
        any model call and a resumed chapter can be skipped outright.
        """
        entry = self._sheet.current_for(chapter.number, known_before_index=chapter.index)
        parts = [
            LEVEL,
            TEMPLATE_VERSION,
            self._brief_key(chapter),
            entry.fingerprint() if entry else "no-sheet-entry",
            os.environ.get("FREELLMAPI_IMAGE_MODEL", ""),
            build_prompt(self._book_title, chapter),
        ]
        return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()

    def prompt_and_hash(self, chapter: Chapter) -> tuple[str, str]:
        """The image prompt for ``chapter`` and the fingerprint to record for it.

        Never raises. When no brief can be had, returns the level-1 prompt with the
        level-1 fingerprint, so a later run retries the enhanced level.
        """
        if self._disabled:
            return self._template_prompt_and_hash(chapter)
        try:
            content_hash = self.content_hash(chapter)
            brief = self._brief_for(chapter)
            self._remember_look(brief, chapter)
            scene = describe_scene(brief, self._cast)
            prompt = build_prompt(self._book_title, chapter, scene=scene)
        except Exception as exc:  # noqa: BLE001 - any brief failure must fall back to level 1
            logger.warning(
                "Scene brief unavailable for chapter %d, using the template prompt: %s",
                chapter.index,
                exc,
            )
            self._record_failure()
            return self._template_prompt_and_hash(chapter)
        self._consecutive_failures = 0
        return prompt, content_hash

    def _template_prompt_and_hash(self, chapter: Chapter) -> tuple[str, str]:
        return (
            build_prompt(self._book_title, chapter),
            chapter_content_hash(chapter, self._book_title),
        )

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self._disabled = True
            logger.warning(
                "Scene briefs failed %d chapters in a row: the rest of this job uses the "
                "template prompt",
                self._consecutive_failures,
            )

    def _brief_key(self, chapter: Chapter) -> str:
        return brief_cache_key(
            book_title=self._book_title,
            chapter=chapter,
            cast=self._cast,
            sheet_entry=self._sheet.current_for(chapter.number, known_before_index=chapter.index),
        )

    def _brief_for(self, chapter: Chapter) -> SceneBrief:
        key = self._brief_key(chapter)
        cached = self._briefs.get(key)
        if cached is not None:
            try:
                return parse_scene_brief(
                    cached, cast=self._cast, chapter_text=chapter_full_text(chapter)
                )
            except SceneBriefError:
                logger.info("Stored scene brief no longer validates, asking again")
        brief = request_scene_brief(
            self._client,
            book_title=self._book_title,
            chapter=chapter,
            cast=self._cast,
            sheet_entry=self._sheet.current_for(chapter.number, known_before_index=chapter.index),
        )
        self._briefs.put(key, brief.to_dict())
        return brief

    def _remember_look(self, brief: SceneBrief, chapter: Chapter) -> None:
        if brief.appearance_update is not None:
            self._sheet.apply_update(
                brief.appearance_update, chapter_number=chapter.number, chapter_index=chapter.index
            )


def build_enhanced_thumbnails(work_dir: str, book_title: str) -> EnhancedThumbnails | None:
    """The enhanced level, or ``None`` (logged) when chat credentials are missing.

    A job that asked for it still gets its thumbnails, at level 1.
    """
    try:
        require_llm_attribution_configured()
    except RuntimeError as exc:
        logger.warning("Enhanced thumbnails unavailable, using the template prompt: %s", exc)
        return None
    client = LLMClient(task="light", timeout=BRIEF_TIMEOUT_SECONDS)
    return EnhancedThumbnails(client, work_dir, book_title)
