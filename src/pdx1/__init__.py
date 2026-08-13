"""
PDX-1i — Portland Metro Intelligence platform.

The schema names are re-exported eagerly; `models` is dependency-free and cheap.
The pipeline names are resolved on first use instead, for two reasons:

1. `python -m pdx1.pipeline` -- the command the README leads with -- warns and runs
   the module body twice if importing the package has already put `pdx1.pipeline` in
   `sys.modules`. runpy imports the package, finds the submodule already there, then
   executes the same file again as `__main__`.
2. Importing `pdx1` for a schema should not drag in config, gates, graph, the
   neutrality gates, publication and the store.

Every public name still resolves the same way, so `from pdx1 import Pipeline` is
unchanged from a caller's side.
"""

from typing import TYPE_CHECKING

__version__ = "0.1.0"

from .models import (
    AnomalyTier,
    ConfidenceTier,
    IntelligenceRecord,
    Outcome,
    PipelineRunSummary,
    Priority,
    Signal,
    SourceType,
)

if TYPE_CHECKING:  # import for type checkers only -- no runtime cost
    from .pipeline import CycleResult, Pipeline, run_cycle

#: Names served from `pdx1.pipeline` on first attribute access.
_LAZY = {"CycleResult", "Pipeline", "run_cycle"}


def __getattr__(name: str):
    """Resolve the pipeline re-exports on demand (PEP 562)."""
    if name in _LAZY:
        from . import pipeline

        return getattr(pipeline, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "__version__",
    "AnomalyTier",
    "ConfidenceTier",
    "CycleResult",
    "IntelligenceRecord",
    "Outcome",
    "Pipeline",
    "PipelineRunSummary",
    "Priority",
    "Signal",
    "SourceType",
    "run_cycle",
]
