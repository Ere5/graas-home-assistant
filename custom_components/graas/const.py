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
# A controller, zone or optional entity goes only after this many successful polls
# in a row without it: one /state that leaves something out could be a GRAAS hiccup.
MISSING_POLLS_BEFORE_REMOVAL: Final = 2

# Run time used when a zone valve is opened from HA (per-zone number entity).
DEFAULT_RUN_MINUTES: Final = 15
# Mirror the server's one limit on a single run (web-server IrrigationLimits:
# MAX_RUN_MINUTES = 300, the firmware's 5 h safety stop; MAX_RUN_LITERS = 1000,
# litres x plants on per-plant controllers). Keep them in step with the server.
MAX_RUN_MINUTES: Final = 300
MAX_RUN_LITERS: Final = 1000

# After a valve command, the valve shows "opening"/"closing" until GRAAS reports
# the zone watering (or not), at most this long. The controller takes a few
# seconds to pick the task up, so GRAAS is asked again after these delays.
OPTIMISTIC_TIMEOUT: Final = timedelta(seconds=60)
FOLLOW_UP_REFRESH_DELAYS: Final = (timedelta(seconds=5), timedelta(seconds=15))

TOKEN_PREFIX: Final = "graas_pat_"
# Longest rain delay GRAAS accepts.
MAX_RAIN_DELAY_DAYS: Final = 14
