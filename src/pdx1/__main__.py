"""
Package entry point, so `python -m pdx1` runs a cycle.

Equivalent to the `pdx1` console script and to `python -m pdx1.pipeline`.
"""

from __future__ import annotations

import sys

from .pipeline import main

if __name__ == "__main__":
    sys.exit(main())
