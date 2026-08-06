"""
Neutrality checks.

Three run on every section, but only one of them withholds anything:

- **attribution** — a **gate**. A section citing nothing, or citing a record the
  engine does not hold, does not publish. Traceability is the one claim the engine
  makes about every line it prints, so this one still rejects.
- **tone** — **observation only.** Matches language that asserts wrongdoing and
  records what it found.
- **hedging** — **observation only.** Matches language that implies wrongdoing without
  asserting it, which neither of the others can see.

Tone and hedging were gates until a live run showed the flaw: they scan the assembled
section body, and a record's `pattern` carries harvested source text into it, so a
newspaper reporting a guilty plea tripped the same vocabulary as the engine alleging
one. They could not tell those apart, and withholding the section suppressed the
report to prevent the accusation.

What that trades away is worth being plain about: nothing now stops prosecutorial or
insinuating language reaching a reader. Both checks annotate; neither refuses. The
observations ride on the published section and into the store, so the judgement moves
to whoever reads them.
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
