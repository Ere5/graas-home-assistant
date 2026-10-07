"""Constants for the GRAAS Irrigation integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "graas"

CONF_API_URL: Final = "api_url"
DEFAULT_API_URL: Final = "https://ss.graasautomation.com"

# The GRAAS API returns everything in one call; 30 s keeps HA current
# without loading the server (a command triggers an immediate refresh).
UPDATE_INTERVAL: Final = timedelta(seconds=30)

# Run time used when a zone valve is opened from HA (per-zone number entity).
DEFAULT_RUN_MINUTES: Final = 15
# Mirrors the server's limit for runs started from Home Assistant.
MAX_RUN_MINUTES: Final = 120
MAX_RUN_LITERS: Final = 2000

TOKEN_PREFIX: Final = "graas_pat_"
# Longest rain delay GRAAS accepts.
MAX_RAIN_DELAY_DAYS = 14
