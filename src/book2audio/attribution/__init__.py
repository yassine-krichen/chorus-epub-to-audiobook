"""Speaker attribution: decide who says each line of dialogue.

The default :class:`HeuristicAttributor` is deterministic and offline. The
:class:`LLMAttributor` layers a language model on top of it (``llm`` or
``hybrid`` mode). Both implement :class:`Attributor`; use
:func:`build_attributor` to pick one from a :class:`~book2audio.config.Config`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from book2audio.attribution.base import ATTRIBUTOR_MODES, VOICE_MODES, Attributor
from book2audio.attribution.gender import (
    GENDER_CONFIDENCE_THRESHOLD,
    GenderEvidence,
    GenderVerdict,
    resolve_genders,
    validate_gender_overrides,
)
from book2audio.attribution.heuristic import HeuristicAttributor
from book2audio.attribution.llm import CACHE_FILENAME, AttributionCache, LLMAttributor
from book2audio.attribution.llm_client import (
    LLMClient,
    LLMError,
    require_llm_attribution_configured,
)

if TYPE_CHECKING:
    from book2audio.config import Config

__all__ = [
    "ATTRIBUTOR_MODES",
    "GENDER_CONFIDENCE_THRESHOLD",
    "VOICE_MODES",
    "Attributor",
    "AttributionCache",
    "GenderEvidence",
    "GenderVerdict",
    "HeuristicAttributor",
    "LLMAttributor",
    "LLMClient",
    "LLMError",
    "build_attributor",
    "require_llm_attribution_configured",
    "resolve_genders",
    "validate_gender_overrides",
]


def build_attributor(
    config: Config,
    *,
    only_chapters: frozenset[int] | None = None,
    on_progress: Callable[[], None] | None = None,
) -> HeuristicAttributor | LLMAttributor:
    """Create the attributor ``config.attributor`` names.

    ``only_chapters`` limits LLM calls to the chapters being rendered; the
    heuristic baseline still covers the whole book. ``on_progress`` is called
    after every LLM window so a long pass can report that it is alive.
    """
    if config.attributor == "heuristic":
        return HeuristicAttributor(config.gender_overrides)
    if config.attributor not in ATTRIBUTOR_MODES:
        raise ValueError(f"Unknown attributor {config.attributor!r}")
    return LLMAttributor(
        config.attributor,
        LLMClient(task="attribution"),
        AttributionCache(os.path.join(config.work_dir, CACHE_FILENAME)),
        config.attribution_max_calls,
        only_chapters=only_chapters,
        on_progress=on_progress,
        gender_overrides=config.gender_overrides,
        # Short, forgiving jobs: the light chain is cheaper than the attribution one.
        judge_client=LLMClient(task="light"),
    )
