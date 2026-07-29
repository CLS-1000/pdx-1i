"""Source adapters for the PDX-1i feeds."""

from .base import FetchResult, SourceAdapter
from .olis import OlisAdapter
from .orestar import OrestarAdapter
from .portland_press import PortlandPressAdapter
from .sei import SeiAdapter
from .wa_pdc import WaPdcAdapter

__all__ = [
    "FetchResult",
    "SourceAdapter",
    "OlisAdapter",
    "OrestarAdapter",
    "PortlandPressAdapter",
    "SeiAdapter",
    "WaPdcAdapter",
]
