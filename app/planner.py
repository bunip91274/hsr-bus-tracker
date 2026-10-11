"""Direct-trip planner: residence -> destination stop on a single bus.

For every bus leaving a stop near your residence, look ahead along that trip for a stop
near your destination. Each match becomes an option with:
    leave by  = bus departure - walk to stop - buffer
    arrive    = bus arrival at the stop near your destination + walk from there
Live predictions (delays, cancellations) are applied to both ends.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .departures import predict, service_day_origin
from .geo import haversine_m
from .gtfs_static import StaticFeed, Stop
from .realtime import TripUpdateInfo
from .residences import NearbyStop

WALK_M_PER_S = 1.3  # ~80 m/min, a normal walking pace


def walk_time(metres: float) -> timedelta:
    return timedelta(seconds=round(metres / WALK_M_PER_S))


@dataclass(frozen=True)
class TripOption:
    trip_id: str
    route_short_name: str
    route_color: str
    route_text_color: str
    headsign: str
    board_stop: Stop
    walk_to_stop: timedelta
    alight_stop: Stop
    walk_from_stop: timedelta
    leave_by: datetime  # UTC
    depart: datetime  # UTC, bus leaves the boarding stop
    arrive_stop: datetime  # UTC, bus reaches the alighting stop
    arrive: datetime  # UTC, you reach the destination
    live: bool
    num_stops: int


def destination_area(feed: StaticFeed, dest_stop_id: str, radius_m: float) -> dict[str, float]:
    """The chosen stop plus any stop within radius (other side of the street, etc.) -> metres to walk."""
    dest = feed.stops[dest_stop_id]
    return {
        sid: d
        for sid, s in feed.stops.items()
        if (d := haversine_m(dest.lat, dest.lon, s.lat, s.lon)) <= radius_m
    }


def plan_trips(
    feed: StaticFeed,
    origin_stops: list[NearbyStop],
    dest_stop_id: str,
    now: datetime,
    realtime: dict[str, TripUpdateInfo],
    tz: ZoneInfo,
    arrive_by: datetime | None = None,
    horizon: timedelta = timedelta(hours=3),
    buffer: timedelta = timedelta(minutes=2),
    dest_radius_m: float = 250,
    limit: int = 4,
) -> list[TripOption]:
    now = now.astimezone(timezone.utc)
    latest_departure = arrive_by.astimezone(timezone.utc) if arrive_by else now + horizon
    dest = destination_area(feed, dest_stop_id, dest_radius_m)
    local_today = now.astimezone(tz).date()

    best: dict[tuple[str, object], TripOption] = {}
    for service_date in (local_today - timedelta(days=1), local_today, local_today + timedelta(days=1)):
        active = feed.active_service_ids(service_date)
        if not active:
            continue
        origin = service_day_origin(service_date, tz)

        for ns in origin_stops:
            walk_in = walk_time(ns.distance_m)
            for st in feed.stop_times_by_stop.get(ns.stop.stop_id, []):
                trip = feed.trips.get(st.trip_id)
                if trip is None or trip.service_id not in active:
                    continue
                sched_dep = origin + timedelta(seconds=st.departure_secs)
                if sched_dep < now - timedelta(minutes=30) or sched_dep > latest_departure + timedelta(minutes=30):
                    continue

                tu = realtime.get(st.trip_id)
                pred_dep, canceled = predict(st, sched_dep, tu)
                if canceled:
                    continue
                depart = pred_dep or sched_dep
                leave_by = depart - walk_in - buffer
                if leave_by < now - timedelta(minutes=1) or depart > latest_departure:
                    continue

                # Ride forward along this trip looking for the destination.
                stops_after = [s for s in feed.stop_times_by_trip.get(st.trip_id, []) if s.stop_sequence > st.stop_sequence]
                for n, later in enumerate(stops_after, start=1):
                    if later.stop_id not in dest:
                        continue
                    sched_arr = origin + timedelta(seconds=later.departure_secs)
                    pred_arr, skipped = predict(later, sched_arr, tu)
                    if skipped:
                        continue
                    arrive_stop = pred_arr or sched_arr
                    walk_out = walk_time(dest[later.stop_id])
                    route = feed.routes.get(trip.route_id)
                    option = TripOption(
                        trip_id=st.trip_id,
                        route_short_name=route.short_name if route else trip.route_id,
                        route_color=route.color if route else "1F4E79",
                        route_text_color=route.text_color if route else "FFFFFF",
                        headsign=trip.headsign or (route.long_name if route else ""),
                        board_stop=ns.stop,
                        walk_to_stop=walk_in,
                        alight_stop=feed.stops[later.stop_id],
                        walk_from_stop=walk_out,
                        leave_by=leave_by,
                        depart=depart,
                        arrive_stop=arrive_stop,
                        arrive=arrive_stop + walk_out,
                        live=pred_dep is not None or pred_arr is not None,
                        num_stops=n,
                    )
                    key = (st.trip_id, service_date)
                    cur = best.get(key)
                    # Same bus, several ways to catch it: prefer arriving earliest, then leaving latest.
                    if cur is None or (option.arrive, -option.leave_by.timestamp()) < (cur.arrive, -cur.leave_by.timestamp()):
                        best[key] = option
                    break  # first stop in the destination area is the one to get off at

    options = list(best.values())
    if arrive_by:
        options = [o for o in options if o.arrive <= arrive_by.astimezone(timezone.utc)]

    # Drop options that are strictly worse: leave earlier AND arrive later than another one.
    options.sort(key=lambda o: (o.arrive, -o.leave_by.timestamp()))
    useful: list[TripOption] = []
    for o in options:
        if not useful or o.leave_by > max(u.leave_by for u in useful):
            useful.append(o)

    if arrive_by:
        useful.sort(key=lambda o: o.leave_by, reverse=True)  # the latest you can get away with first
    return useful[:limit]
