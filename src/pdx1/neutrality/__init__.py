"""
Neutrality gates.

Three checks stand between an analyzed record and a published section, and each
catches what the others cannot:

- **tone** rejects language that asserts wrongdoing.
- **hedging** rejects language that implies it without asserting anything -- which is
  invisible to the other two, because an implication makes no claim to source.
- **attribution** rejects claims that trace to no record the engine holds.

All three run on every section, and a section that fails any of them does not appear
in the brief.
"""

from .attribution import AttributionResult, check_attribution
from .hedging import HedgingResult, check_hedging
from .tone import ToneResult, check_tone

__all__ = [
    "AttributionResult",
    "HedgingResult",
    "ToneResult",
    "check_attribution",
    "check_hedging",
    "check_tone",
]
