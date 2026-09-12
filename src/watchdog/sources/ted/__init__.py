"""TED source adapter: client, query builder, field mapping.

The contract this implements is docs/TED_API_CONTRACT.md. If TED changes, that
document and this package change together and nothing else does.
"""

from watchdog.sources.ted.client import TedClient
from watchdog.sources.ted.config import TedSourceConfig, load_ted_config, parse_ted_config
from watchdog.sources.ted.mapper import REQUESTED_FIELDS, map_notice
from watchdog.sources.ted.query import build_query
from watchdog.sources.ted.source import TedSource

__all__ = [
    "REQUESTED_FIELDS",
    "TedClient",
    "TedSource",
    "TedSourceConfig",
    "build_query",
    "load_ted_config",
    "map_notice",
    "parse_ted_config",
]
