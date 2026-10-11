"""Fetch and parse HSR's GTFS-Realtime TripUpdates feed (protobuf)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx
from google.transit import gtfs_realtime_pb2

log = logging.getLogger(__name__)

SKIPPED = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.SKIPPED
CANCELED = gtfs_realtime_pb2.TripDescriptor.CANCELED


@dataclass(frozen=True)
class StopUpdate:
    stop_sequence: int | None
    stop_id: str | None
    time: int | None  # absolute POSIX seconds, if the feed gives one
    delay: int | None  # seconds late (negative = early), if the feed gives one
    skipped: bool = False


@dataclass
class TripUpdateInfo:
    trip_id: str
    canceled: bool = False
    trip_delay: int | None = None
    updates: list[StopUpdate] = field(default_factory=list)


def _event(stu) -> tuple[int | None, int | None]:
    # Departure is what a rider waiting at the stop cares about; fall back to arrival.
    for name in ("departure", "arrival"):
        if stu.HasField(name):
            ev = getattr(stu, name)
            t = ev.time if ev.HasField("time") and ev.time else None
            d = ev.delay if ev.HasField("delay") else None
            if t is not None or d is not None:
                return t, d
    return None, None


def parse_trip_updates(raw: bytes) -> dict[str, TripUpdateInfo]:
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.ParseFromString(raw)
    out: dict[str, TripUpdateInfo] = {}
    for entity in msg.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        trip_id = tu.trip.trip_id
        if not trip_id:
            continue
        info = TripUpdateInfo(
            trip_id=trip_id,
            canceled=tu.trip.schedule_relationship == CANCELED,
            trip_delay=tu.delay if tu.HasField("delay") else None,
        )
        for stu in tu.stop_time_update:
            t, d = _event(stu)
            info.updates.append(
                StopUpdate(
                    stop_sequence=stu.stop_sequence if stu.HasField("stop_sequence") else None,
                    stop_id=stu.stop_id or None,
                    time=t,
                    delay=d,
                    skipped=stu.schedule_relationship == SKIPPED,
                )
            )
        out[trip_id] = info
    return out


class RealtimeCache:
    """Fetches the feed at most once per TTL. If a fetch fails, keeps serving the last good copy."""

    def __init__(self, url: str, ttl_seconds: int):
        self.url = url
        self.ttl = ttl_seconds
        self._data: dict[str, TripUpdateInfo] = {}
        self._fetched_at: float | None = None
        self._last_attempt = 0.0
        self.last_error: str | None = None

    @property
    def age_seconds(self) -> float | None:
        return None if self._fetched_at is None else time.time() - self._fetched_at

    async def get(self, client: httpx.AsyncClient) -> dict[str, TripUpdateInfo]:
        if time.time() - self._last_attempt < self.ttl:
            return self._data
        self._last_attempt = time.time()
        try:
            r = await client.get(self.url, timeout=10)
            r.raise_for_status()
            self._data = parse_trip_updates(r.content)
            self._fetched_at = time.time()
            self.last_error = None
        except Exception as exc:  # network blips shouldn't take the app down
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("realtime fetch failed: %s", self.last_error)
        return self._data
