"""Central configuration. Everything can be overridden with environment variables."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent

# Official City of Hamilton open-data feeds (see hamilton.ca open data licence).
STATIC_GTFS_URL = os.getenv(
    "HSR_STATIC_URL", "https://opendata.hamilton.ca/GTFS-Static/google_transit.zip"
)
TRIP_UPDATES_URL = os.getenv(
    "HSR_TRIP_UPDATES_URL", "https://opendata.hamilton.ca/GTFS-RT/GTFS_TripUpdates.pb"
)

# Where the downloaded static feed is cached between runs.
CACHE_DIR = Path(os.getenv("HSR_CACHE_DIR", ROOT / ".cache"))
STATIC_MAX_AGE_HOURS = int(os.getenv("HSR_STATIC_MAX_AGE_HOURS", "24"))

RESIDENCES_FILE = Path(os.getenv("HSR_RESIDENCES_FILE", ROOT / "app" / "data" / "residences.json"))

TIMEZONE = ZoneInfo("America/Toronto")

# How far (metres) someone will walk from res to a stop, and how many stops to show.
NEARBY_RADIUS_M = int(os.getenv("HSR_NEARBY_RADIUS_M", "500"))
MAX_STOPS_PER_RESIDENCE = int(os.getenv("HSR_MAX_STOPS", "5"))

# Look-ahead window and per-stop cap for departures.
WINDOW_MINUTES = int(os.getenv("HSR_WINDOW_MINUTES", "90"))
DEPARTURES_PER_STOP = int(os.getenv("HSR_DEPARTURES_PER_STOP", "6"))

# Don't hammer the city's server: reuse the realtime feed for this many seconds.
REALTIME_TTL_SECONDS = int(os.getenv("HSR_REALTIME_TTL", "30"))
