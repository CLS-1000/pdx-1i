"""Source adapters for the PDX-1i feeds."""

from .base import FetchResult, LiveSourceAdapter, SourceAdapter
from .olis import OlisAdapter
from .orestar import OrestarAdapter
from .portland_press import PortlandPressAdapter
from .sei import SeiAdapter
from .wa_pdc import WaPdcAdapter

__all__ = [
    "FetchResult",
    "LiveSourceAdapter",
    "SourceAdapter",
    "OlisAdapter",
    "OrestarAdapter",
    "PortlandPressAdapter",
    "SeiAdapter",
    "WaPdcAdapter",
]
