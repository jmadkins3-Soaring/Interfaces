"""Dark Web Threat Intelligence — standalone consulting tool.

Searches Intelligence X (intelx.io) for dark web, breach, leak, and paste
exposure relating to a third-party company you supply. Intended for personal
/ consulting due-diligence and third-party risk monitoring.
"""

__version__ = "1.0.0"

from .client import IntelXClient, IntelXError
from .analyzer import ThreatAnalyzer, ThreatFinding, Severity

__all__ = [
    "IntelXClient",
    "IntelXError",
    "ThreatAnalyzer",
    "ThreatFinding",
    "Severity",
    "__version__",
]
