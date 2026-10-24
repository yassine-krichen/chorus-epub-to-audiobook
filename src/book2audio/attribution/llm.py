"""LLM speaker attribution, layered on top of the heuristic baseline.

The :class:`HeuristicAttributor` always runs first. Then, per window of
consecutive segments, the model is asked who speaks each target dialogue line:

* ``llm`` mode targets every dialogue line.
* ``hybrid`` mode targets only the lines the heuristic had to guess (pronoun,
  alternation or fallback tiers); lines with an explicit name in the text keep
  their heuristic label and cost no tokens.

The model is never trusted blindly. Replies are validated line by line and
anything missing or invalid keeps the heuristic label, so a bad reply, an
outage or an exhausted budget degrades to the heuristic result instead of
failing the render. Responses are cached on disk (keyed by prompt, model and
window text) so re-running a book costs nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from book2audio.attribution.heuristic import _STOPWORDS, HeuristicAttributor
from book2audio.attribution.llm_client import LLMClient, LLMError
from book2audio.models import NARRATOR, Chapter, SegmentKind

MAX_KNOWN_NAMES = 30
MAX_DIALOGUE_LINES_PER_WINDOW = 25
MAX_CHARS_PER_WINDOW = 6000
CONTEXT_SEGMENTS = 3
# Context is read-only background, so long paragraphs are clipped to keep the
# prompt small (a gateway may reject large prompts outright).
MAX_CONTEXT_CHARS = 400
MAX_CONSECUTIVE_FAILURES = 3
CACHE_FILENAME = "attribution-cache.json"

# Tiers that are guesses rather than a name found in the text.
UNSURE_TIERS = frozenset({"pronoun", "alternation", "fallback"})

SYSTEM_PROMPT = (
    "You attribute quoted dialogue in a novel to the character who speaks it.\n"
    "You get a numbered passage and a list of ids to label. Lines marked "
    "'context only' or 'already attributed' are background; never label them.\n"
    "Rules:\n"
    "- Use the speaker's name exactly as in the known-characters list when the "
    "speaker is one of them.\n"
    "- For a character not in the list, use a name that appears capitalized in "
    "the passage. Never invent names.\n"
    '- Use "Narrator" only if the quoted text is not actually spoken by a character.\n'
    "- If you cannot tell who speaks a line, leave its id out.\n"
    "- Only label ids from the 'Label dialogue ids' list.\n"
    'Reply with JSON only, no prose: {"labels":[{"id":3,"speaker":"Alice"}]}'
)

_NAME_PUNCTUATION = frozenset(" .'’-")
MAX_NAME_LENGTH = 40


def prompt_fingerprint() -> str:
    """Hash of the instructions sent to the model, recorded with eval results."""
    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


@dataclass
class AttributionReport:
    """What the LLM stage did, for one CLI line and for evaluation."""

    mode: str
    windows: int = 0  # windows that had at least one target line
    windows_skipped: int = 0  # dialogue windows with no target line (hybrid)
    windows_failed: int = 0
    windows_unprocessed: int = 0  # not attempted: budget exhausted or breaker open
    calls: int = 0
    cache_hits: int = 0
    lines_targeted: int = 0
    lines_changed: int = 0
    budget_exhausted: bool = False
    breaker_tripped: bool = False
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"LLM attribution ({self.mode}): {self.calls} call(s), "
            f"{self.cache_hits} cache hit(s), {self.windows} window(s) "
            f"({self.windows_skipped} skipped, {self.windows_failed} failed, "
            f"{self.windows_unprocessed} unprocessed), {self.lines_targeted} line(s) "
            f"targeted, {self.lines_changed} changed, budget exhausted: "
            f"{'yes' if self.budget_exhausted else 'no'}"
        )


class AttributionCache:
    """JSON file of raw model labels per window, written atomically.

    ``path=None`` keeps entries in memory only. A missing or corrupt file just
    starts empty: the cache is an optimisation, never a source of truth.
    """

    def __init__(self, path: str | None) -> None:
        self.path = path
        self._entries: dict[str, list] = {}
        self.write_error: str | None = None
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    loaded = json.load(fh)
                entries = loaded.get("entries") if isinstance(loaded, dict) else None
                if isinstance(entries, dict):
                    self._entries = {k: v for k, v in entries.items() if isinstance(v, list)}
            except (OSError, ValueError):
                self._entries = {}

    def get(self, key: str) -> list | None:
        return self._entries.get(key)

    def put(self, key: str, labels: list) -> None:
        self._entries[key] = labels
        if not self.path:
            return
        # Unique per process and call, so two runs sharing a work dir cannot
        # clobber each other's half-written file.
        tmp_path = f"{self.path}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "entries": self._entries}, fh)
            os.replace(tmp_path, self.path)
        except OSError as exc:
            self.write_error = str(exc)
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@dataclass
class _Window:
    chapter: Chapter
    start: int  # core segments are [start, end)
    end: int
    context_start: int  # shown segments are [context_start, context_end)
    context_end: int
    targets: list[int] = field(default_factory=list)  # segment positions in chapter

    def prompt_id(self, position: int) -> int:
        """The 1-based number a segment carries in the rendered passage."""
        return position - self.context_start + 1

    def shown_text(self) -> str:
        """Raw text of every segment shown, used to check that a name is real."""
        shown = self.chapter.segments[self.context_start : self.context_end]
        return "\n".join(seg.text for seg in shown)


class LLMAttributor:
    """See module docstring. ``attribute`` never raises because of the LLM."""

    def __init__(
        self,
        mode: str,
        client: LLMClient | None,
        cache: AttributionCache | None = None,
        budget: int = 150,
        *,
        only_chapters: frozenset[int] | None = None,
        on_progress: Callable[[], None] | None = None,
    ) -> None:
        if mode not in ("llm", "hybrid"):
            raise ValueError(f"LLMAttributor mode must be 'llm' or 'hybrid', got {mode!r}")
        self.mode = mode
        self._client = client
        self._cache = cache
        self._budget = budget
        # Restrict LLM calls to these chapter indices (the chapters actually
        # being rendered) so a partial render does not spend the budget on the rest.
        self._only_chapters = only_chapters
        # Called after every window (even skipped or failed ones) so a caller
        # can prove liveness during a long attribution pass.
        self._on_progress = on_progress
        self._heuristic = HeuristicAttributor()
        self.genders: dict[str, str] = {}
        self.report = AttributionReport(mode=mode)

    def estimate(self, chapters: list[Chapter]) -> list[str]:
        """Dry run: heuristic labels plus how many windows the LLM would see.

        Makes no request and reads no cache; ``report.windows`` is an upper
        bound on ``calls`` (cache hits can only lower it).
        """
        names = self._heuristic.attribute(chapters)
        self.genders = self._heuristic.genders
        self.report = AttributionReport(mode=self.mode)
        self._count_windows(self._build_windows(chapters))
        return names

    def attribute(self, chapters: list[Chapter]) -> list[str]:
        self._heuristic.attribute(chapters)
        self.report = report = AttributionReport(mode=self.mode)
        known = self._known_names(chapters)

        # Snapshot before any change: prompts and the changed-lines count must
        # not depend on what earlier windows already rewrote.
        before = {
            (c.index, i): seg.speaker
            for c in chapters
            for i, seg in enumerate(c.segments)
            if seg.kind == SegmentKind.DIALOGUE
        }

        windows = self._build_windows(chapters)
        self._count_windows(windows)
        consecutive_failures = 0

        for window in windows:
            if not window.targets:
                continue
            try:
                outcome = self._attribute_window(window, before, known)
            except Exception as exc:  # noqa: BLE001 - a bug on the LLM side must never fail a render
                outcome = "failed"
                report.windows_failed += 1
                if not any("unexpected error" in w for w in report.warnings):
                    report.warnings.append(
                        f"unexpected error in LLM attribution ({type(exc).__name__}); "
                        "affected windows keep their heuristic labels"
                    )
            if outcome == "failed":
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    report.breaker_tripped = True
            elif outcome == "answered":
                consecutive_failures = 0
            if self._on_progress is not None:
                # Callers must pass a hook that never raises (build_heartbeat swallows DB errors).
                self._on_progress()

        # Fold nicknames the model may have introduced ("Neph" -> "Nephis"),
        # exactly as the heuristic does for its own names.
        self._heuristic.merge_aliases(self._speaker_names(chapters), chapters)

        report.lines_changed = sum(
            1
            for c in chapters
            for i, seg in enumerate(c.segments)
            if seg.kind == SegmentKind.DIALOGUE and seg.speaker != before[(c.index, i)]
        )
        self._finish_warnings(report)

        names = self._speaker_names(chapters)
        self.genders = self._heuristic.infer_genders(names, chapters)
        return names

    def _attribute_window(self, window: _Window, before: dict, known: list[str]) -> str:
        """Label one window. Returns ``cached``, ``answered``, ``failed`` or ``unprocessed``.

        ``failed`` and ``answered`` feed the circuit breaker; ``cached`` and
        ``unprocessed`` involve no request, so they say nothing about the model.
        """
        report = self.report
        window_text = self._render_window(window, before)
        user_prompt = self._user_prompt(window, window_text, known)
        key = self._cache_key(user_prompt)

        labels = self._cache.get(key) if self._cache else None
        if labels is not None:
            report.cache_hits += 1
            self._apply_labels(window, labels, known)
            return "cached"

        if report.breaker_tripped or self._client is None:
            report.windows_unprocessed += 1
            return "unprocessed"
        if report.calls >= self._budget:
            report.budget_exhausted = True
            report.windows_unprocessed += 1
            return "unprocessed"

        report.calls += 1
        try:
            labels = _extract_labels(self._client.complete_json(SYSTEM_PROMPT, user_prompt))
        except LLMError:
            report.windows_failed += 1
            return "failed"
        applied = self._apply_labels(window, labels, known)
        # An empty or fully rejected reply taught us nothing worth replaying.
        if applied and self._cache:
            self._cache.put(key, labels)
        return "answered"

    # --- windows ----------------------------------------------------------

    def _build_windows(self, chapters: list[Chapter]) -> list[_Window]:
        windows: list[_Window] = []
        for chapter in chapters:
            if self._only_chapters is not None and chapter.index not in self._only_chapters:
                continue
            for run_start, run_end in _scene_runs(chapter):
                for start, end in _split_run(chapter, run_start, run_end):
                    window = _Window(
                        chapter=chapter,
                        start=start,
                        end=end,
                        context_start=max(run_start, start - CONTEXT_SEGMENTS),
                        context_end=min(run_end, end + CONTEXT_SEGMENTS),
                    )
                    window.targets = [i for i in range(start, end) if self._is_target(chapter, i)]
                    windows.append(window)
        return windows

    def _is_target(self, chapter: Chapter, position: int) -> bool:
        if chapter.segments[position].kind != SegmentKind.DIALOGUE:
            return False
        if self.mode == "llm":
            return True
        return self._heuristic.tiers.get((chapter.index, position)) in UNSURE_TIERS

    def _count_windows(self, windows: list[_Window]) -> None:
        for window in windows:
            if window.targets:
                self.report.windows += 1
                self.report.lines_targeted += len(window.targets)
            else:
                self.report.windows_skipped += 1

    # --- prompt -----------------------------------------------------------

    def _render_window(self, window: _Window, heuristic_speakers: dict) -> str:
        segments = window.chapter.segments
        targets = set(window.targets)
        lines: list[str] = []
        for position in range(window.context_start, window.context_end):
            seg = segments[position]
            number = window.prompt_id(position)
            in_core = window.start <= position < window.end
            text = seg.text if in_core else _clip(seg.text, keep_tail=position < window.start)
            if seg.kind == SegmentKind.NARRATION:
                tag = "NARRATION" if in_core else "NARRATION (context only)"
                lines.append(f"[{number}] {tag}: {text}")
            elif position in targets:
                lines.append(f'[{number}] DIALOGUE: "{text}"')
            else:
                status = self._status(window, position, heuristic_speakers)
                lines.append(f'[{number}] DIALOGUE ({status}): "{text}"')
        return "\n".join(lines)

    def _status(self, window: _Window, position: int, heuristic_speakers: dict) -> str:
        """Annotation for a dialogue line the model is not asked to label."""
        tier = self._heuristic.tiers.get((window.chapter.index, position))
        if self.mode == "hybrid" and tier not in UNSURE_TIERS:
            # Only explicit, name-in-text labels are shown; guesses would
            # just feed the heuristic's mistakes back to the model.
            return f"already attributed to {heuristic_speakers[(window.chapter.index, position)]}"
        return "context only"

    @staticmethod
    def _user_prompt(window: _Window, window_text: str, known: list[str]) -> str:
        ids = ", ".join(str(window.prompt_id(position)) for position in window.targets)
        return (
            f"Known characters: {'; '.join(known) if known else '(none yet)'}\n\n"
            f"Passage:\n{window_text}\n\n"
            f"Label dialogue ids: {ids}"
        )

    def _cache_key(self, user_prompt: str) -> str:
        """Everything that shapes the request: instructions, model, sampling, prompt."""
        client = self._client
        blob = json.dumps(
            [
                prompt_fingerprint(),
                client.model if client else "",
                client.temperature if client else 0.0,
                client.max_tokens if client else 0,
                user_prompt,
            ],
            ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    # --- applying replies -------------------------------------------------

    def _apply_labels(
        self,
        window: _Window,
        labels: list,
        known: list[str],
    ) -> int:
        """Apply the valid labels and return how many were applied."""
        by_id = {window.prompt_id(position): position for position in window.targets}
        known_lookup = {name.lower(): name for name in known}
        shown_text = window.shown_text()
        # Names found in this window only: a name must be present in the text
        # of every window it is used in, not just the first.
        accepted_new: dict[str, str] = {}
        seen: set[int] = set()
        for item in labels:
            if not isinstance(item, dict):
                continue
            label_id, raw = item.get("id"), item.get("speaker")
            if isinstance(label_id, bool) or not isinstance(label_id, int):
                continue
            if label_id not in by_id or label_id in seen or not isinstance(raw, str):
                continue
            speaker = _resolve_speaker(raw, known_lookup, accepted_new, shown_text)
            if speaker is None:
                continue
            seen.add(label_id)
            window.chapter.segments[by_id[label_id]].speaker = speaker
        return len(seen)

    # --- bookkeeping ------------------------------------------------------

    @staticmethod
    def _known_names(chapters: list[Chapter]) -> list[str]:
        counts: dict[str, int] = {}
        for chapter in chapters:
            for seg in chapter.segments:
                if seg.kind == SegmentKind.DIALOGUE and seg.speaker and seg.speaker != NARRATOR:
                    counts[seg.speaker] = counts.get(seg.speaker, 0) + 1
        ranked = sorted(counts, key=lambda name: (-counts[name], name))
        return ranked[:MAX_KNOWN_NAMES]

    @staticmethod
    def _speaker_names(chapters: list[Chapter]) -> list[str]:
        """Distinct non-narrator dialogue speakers, in order of first appearance."""
        names: dict[str, None] = {}
        for chapter in chapters:
            for seg in chapter.segments:
                if seg.kind == SegmentKind.DIALOGUE and seg.speaker and seg.speaker != NARRATOR:
                    names.setdefault(seg.speaker, None)
        return list(names)

    def _finish_warnings(self, report: AttributionReport) -> None:
        if report.budget_exhausted:
            report.warnings.append(
                f"LLM attribution call budget ({self._budget}) exhausted; "
                f"{report.windows_unprocessed} window(s) keep their heuristic labels"
            )
        if report.breaker_tripped:
            report.warnings.append(
                f"LLM attribution stopped after {MAX_CONSECUTIVE_FAILURES} consecutive "
                f"failures; {report.windows_unprocessed} window(s) keep their heuristic labels"
            )
        if self._cache is not None and self._cache.write_error:
            report.warnings.append(
                f"could not write the attribution cache: {self._cache.write_error}"
            )


# --- module helpers --------------------------------------------------------


def _scene_runs(chapter: Chapter) -> list[tuple[int, int]]:
    """``[start, end)`` ranges of segments between scene breaks."""
    runs: list[tuple[int, int]] = []
    start = 0
    for i, seg in enumerate(chapter.segments):
        if seg.kind == SegmentKind.SCENE_BREAK:
            if i > start:
                runs.append((start, i))
            start = i + 1
    if start < len(chapter.segments):
        runs.append((start, len(chapter.segments)))
    return runs


def _split_run(chapter: Chapter, run_start: int, run_end: int) -> list[tuple[int, int]]:
    """Chunk a scene into windows bounded by dialogue-line and character counts.

    Chunks with no dialogue are dropped: there is nothing to attribute.
    """
    chunks: list[tuple[int, int]] = []
    chunk_start, dialogue, chars = run_start, 0, 0
    for i in range(run_start, run_end):
        seg = chapter.segments[i]
        is_dialogue = seg.kind == SegmentKind.DIALOGUE
        if i > chunk_start and (
            (is_dialogue and dialogue >= MAX_DIALOGUE_LINES_PER_WINDOW)
            or chars + len(seg.text) > MAX_CHARS_PER_WINDOW
        ):
            chunks.append((chunk_start, i))
            chunk_start, dialogue, chars = i, 0, 0
        dialogue += is_dialogue
        chars += len(seg.text)
    chunks.append((chunk_start, run_end))
    return [
        (a, b)
        for a, b in chunks
        if any(chapter.segments[i].kind == SegmentKind.DIALOGUE for i in range(a, b))
    ]


def _clip(text: str, *, keep_tail: bool) -> str:
    """Shorten context text, keeping the end nearest the passage."""
    if len(text) <= MAX_CONTEXT_CHARS:
        return text
    return "…" + text[-MAX_CONTEXT_CHARS:] if keep_tail else text[:MAX_CONTEXT_CHARS] + "…"


def _extract_labels(reply: object) -> list:
    if not isinstance(reply, dict) or not isinstance(reply.get("labels"), list):
        raise LLMError('LLM reply did not have the shape {"labels": [...]}')
    return reply["labels"]


def _resolve_speaker(
    raw: str, known_lookup: dict[str, str], accepted_new: dict[str, str], shown_text: str
) -> str | None:
    """Map a model-supplied name to a final speaker, or None to keep the heuristic label."""
    name = raw.strip().strip("\"'“”‘’").rstrip(".,;:!?").strip()
    if not name:
        return None
    lowered = name.lower()
    if lowered == NARRATOR.lower():
        return NARRATOR
    if lowered in known_lookup:
        return known_lookup[lowered]
    if lowered in accepted_new:
        return accepted_new[lowered]
    # A new name must be backed by the text, otherwise it is a hallucination.
    # Limitation: this does not prove the name is a *person* (a capitalized
    # place or title in the window passes). It only checks that the name is not
    # a pronoun or sentence word, appears capitalized as written, and never
    # appears in lowercase (which would make it an ordinary word).
    if not _looks_like_name(name) or name.split()[0] in _STOPWORDS:
        return None
    if not re.search(rf"(?<!\w){re.escape(name)}(?!\w)", shown_text):
        return None
    if re.search(rf"(?<!\w){re.escape(name.lower())}(?!\w)", shown_text):
        return None
    accepted_new[lowered] = name
    return name


def _looks_like_name(name: str) -> bool:
    return (
        len(name) <= MAX_NAME_LENGTH
        and name[0].isupper()
        and all(ch.isalpha() or ch in _NAME_PUNCTUATION for ch in name)
    )
