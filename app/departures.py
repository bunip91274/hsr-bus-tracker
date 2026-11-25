"""Combine the static schedule with realtime predictions to get upcoming departures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .gtfs_static import StaticFeed, StopTime
from .realtime import TripUpdateInfo


@dataclass(frozen=True)
class Departure:
    trip_id: str
    stop_id: str
    route_short_name: str
    route_color: str
    route_text_color: str
    headsign: str
    scheduled: datetime  # UTC
    predicted: datetime | None  # UTC; None = no live data for this trip
    canceled: bool

    @property
    def best(self) -> datetime:
        return self.predicted or self.scheduled

    @property
    def is_live(self) -> bool:
        return self.predicted is not None

    @property
    def delay_seconds(self) -> int | None:
        if self.predicted is None:
            return None
        return int((self.predicted - self.scheduled).total_seconds())


def service_day_origin(service_date: date, tz: ZoneInfo) -> datetime:
    """GTFS times count from 'noon minus 12h' local time, which handles DST-change days correctly."""
    noon = datetime.combine(service_date, time(12), tzinfo=tz)
    return noon.astimezone(UTC) - timedelta(hours=12)


def predict(st: StopTime, scheduled: datetime, tu: TripUpdateInfo | None) -> tuple[datetime | None, bool]:
    """Return (predicted time or None, canceled?) for one scheduled stop visit."""
    if tu is None:
        return None, False
    if tu.canceled:
        return None, True

    # 1. Exact update for this stop.
    for u in tu.updates:
        if u.stop_sequence == st.stop_sequence or (u.stop_sequence is None and u.stop_id == st.stop_id):
            if u.skipped:
                return None, True
            if u.time is not None:
                return datetime.fromtimestamp(u.time, UTC), False
            if u.delay is not None:
                return scheduled + timedelta(seconds=u.delay), False

    # 2. Propagate the delay from the closest earlier stop that reported one (GTFS-RT convention).
    earlier = [
        u
        for u in tu.updates
        if u.stop_sequence is not None and u.stop_sequence < st.stop_sequence and u.delay is not None
    ]
    if earlier:
        last = max(earlier, key=lambda u: u.stop_sequence)
        return scheduled + timedelta(seconds=last.delay), False

    # 3. Trip-level delay, if the feed only gives that.
    if tu.trip_delay is not None:
        return scheduled + timedelta(seconds=tu.trip_delay), False

    return None, False


def upcoming_departures(
    feed: StaticFeed,
    stop_id: str,
    now: datetime,
    realtime: dict[str, TripUpdateInfo],
    tz: ZoneInfo,
    window: timedelta = timedelta(minutes=90),
    limit: int = 6,
    include_canceled: bool = True,
) -> list[Departure]:
    now = now.astimezone(UTC)
    local_today = now.astimezone(tz).date()
    results: list[Departure] = []

    # Yesterday's service can still be running after midnight (times like 25:30:00),
    # and late at night the next bus may belong to tomorrow's service.
    one_day = timedelta(days=1)
    for service_date in (local_today - one_day, local_today, local_today + one_day):
        active = feed.active_service_ids(service_date)
        if not active:
            continue
        origin = service_day_origin(service_date, tz)
        for st in feed.stop_times_by_stop.get(stop_id, []):
            trip = feed.trips.get(st.trip_id)
            if trip is None or trip.service_id not in active:
                continue
            scheduled = origin + timedelta(seconds=st.departure_secs)
            # Cheap pre-filter before doing realtime work: buses can run late, so allow 30 min slack.
            if scheduled < now - timedelta(minutes=30) or scheduled > now + window:
                continue
            predicted, canceled = predict(st, scheduled, realtime.get(st.trip_id))
            dep_time = predicted or scheduled
            if dep_time < now - timedelta(seconds=30) or dep_time > now + window:
                continue
            if canceled and not include_canceled:
                continue
            route = feed.routes.get(trip.route_id)
            results.append(
                Departure(
                    trip_id=st.trip_id,
                    stop_id=stop_id,
                    route_short_name=route.short_name if route else trip.route_id,
                    route_color=route.color if route else "1F4E79",
                    route_text_color=route.text_color if route else "FFFFFF",
                    headsign=trip.headsign or (route.long_name if route else ""),
                    scheduled=scheduled,
                    predicted=predicted,
                    canceled=canceled,
                )
            )

    results.sort(key=lambda d: d.best)
    return results[:limit]
