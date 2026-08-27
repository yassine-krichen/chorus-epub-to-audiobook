"""Speaker attribution: decide who says each line of dialogue.

The default :class:`HeuristicAttributor` is deterministic and offline. It
implements :class:`Attributor`, so an LLM-backed attributor can be dropped in
later without touching the rest of the pipeline.
"""

from book2audio.attribution.base import Attributor
from book2audio.attribution.heuristic import HeuristicAttributor

__all__ = ["Attributor", "HeuristicAttributor"]
