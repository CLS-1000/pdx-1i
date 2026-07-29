"""
Neutrality gates.

Two checks stand between an analyzed record and a published section: tone and
attribution. Both run on every section, and a section that fails either does not appear
in the brief.
"""

from .attribution import AttributionResult, check_attribution
from .tone import ToneResult, check_tone

__all__ = [
    "AttributionResult",
    "ToneResult",
    "check_attribution",
    "check_tone",
]
