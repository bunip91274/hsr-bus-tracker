from datetime import UTC, date, datetime, timedelta

import pytest

from app.config import TIMEZONE
from app.departures import service_day_origin, upcoming_departures
from app.gtfs_static import load_static, parse_gtfs_time
from app.realtime import parse_trip_updates
from app.residences import Residence, nearby_stops

from .fixtures import make_trip_updates, write_gtfs_zip

TZ = TIMEZONE


@pytest.fixture
def feed(tmp_path):
    return load_static(write_gtfs_zip(tmp_path / "gtfs.zip"), keep_stop=lambda s: s.stop_id != "FAR")


def local(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


# --- static feed -----------------------------------------------------------


def test_parse_gtfs_time_past_midnight():
    assert parse_gtfs_time("25:10:00") == 25 * 3600 + 600


def test_only_wanted_stop_times_kept(feed):
    assert "FAR" not in feed.stop_times_by_stop
    assert {st.trip_id for st in feed.stop_times_by_stop["S2"]} == {"T1", "T2", "T3", "T_LATE"}
    assert "FAR" in feed.stops  # stop metadata is still loaded


def test_calendar_and_holiday_exceptions(feed):
    assert feed.active_service_ids(date(2026, 10, 14)) == {"WKDY"}  # Wednesday
    assert feed.active_service_ids(date(2026, 10, 10)) == {"SAT"}  # Saturday
    assert feed.active_service_ids(date(2026, 10, 12)) == {"SAT"}  # Thanksgiving Monday


def test_dst_origin_is_noon_minus_12h():
    # Nov 1 2026 is the fall-back day (25-hour day). Measuring from real midnight would put
    # GTFS "08:00" at 7:00 wall time; noon-minus-12h keeps it at 8:00 like the paper schedule.
    origin = service_day_origin(date(2026, 11, 1), TZ)
    assert (origin + timedelta(hours=8)).astimezone(TZ).hour == 8
    # On a normal day the origin is just local midnight.
    assert service_day_origin(date(2026, 10, 14), TZ).astimezone(TZ).hour == 0


# --- residences ------------------------------------------------------------


def test_nearby_stops_sorted_and_filtered(feed):
    res = Residence("pgcll", "PGCLL", 43.2603, -79.9228)
    found = nearby_stops(res, feed.stops, radius_m=500, limit=5)
    assert [n.stop.stop_id for n in found] == ["S1", "S2"]
    assert found[0].distance_m < found[1].distance_m


# --- departures ------------------------------------------------------------


def test_schedule_only(feed):
    now = local(2026, 10, 14, 7, 58)
    deps = upcoming_departures(feed, "S2", now, {}, TZ)
    assert [d.trip_id for d in deps] == ["T1", "T3", "T2"]
    assert all(not d.is_live for d in deps)


def test_realtime_delay_reorders(feed):
    rt = parse_trip_updates(make_trip_updates([{"trip_id": "T1", "stops": [{"seq": 2, "delay": 600}]}]))
    now = local(2026, 10, 14, 7, 58)
    deps = upcoming_departures(feed, "S2", now, rt, TZ)
    # T1 is 10 min late (08:15), so T3 (08:10) is now first.
    assert [d.trip_id for d in deps] == ["T3", "T1", "T2"]
    t1 = next(d for d in deps if d.trip_id == "T1")
    assert t1.is_live and t1.delay_seconds == 600


def test_delay_propagates_from_earlier_stop(feed):
    rt = parse_trip_updates(make_trip_updates([{"trip_id": "T2", "stops": [{"seq": 1, "delay": 180}]}]))
    deps = upcoming_departures(feed, "S2", local(2026, 10, 14, 8, 0), rt, TZ)
    t2 = next(d for d in deps if d.trip_id == "T2")
    assert t2.delay_seconds == 180


def test_absolute_time_prediction(feed):
    predicted = local(2026, 10, 14, 8, 12).astimezone(UTC)
    rt = parse_trip_updates(
        make_trip_updates([{"trip_id": "T3", "stops": [{"seq": 1, "time": int(predicted.timestamp())}]}])
    )
    deps = upcoming_departures(feed, "S2", local(2026, 10, 14, 8, 0), rt, TZ)
    t3 = next(d for d in deps if d.trip_id == "T3")
    assert t3.predicted == predicted and t3.delay_seconds == 120


def test_canceled_and_skipped(feed):
    rt = parse_trip_updates(
        make_trip_updates(
            [
                {"trip_id": "T1", "canceled": True},
                {"trip_id": "T3", "stops": [{"seq": 1, "skipped": True}]},
            ]
        )
    )
    now = local(2026, 10, 14, 7, 58)
    deps = upcoming_departures(feed, "S2", now, rt, TZ)
    assert {d.trip_id for d in deps if d.canceled} == {"T1", "T3"}
    hidden = upcoming_departures(feed, "S2", now, rt, TZ, include_canceled=False)
    assert [d.trip_id for d in hidden] == ["T2"]


def test_after_midnight_trip_from_previous_service_day(feed):
    # T_LATE runs at "24:15" on Wednesday's service = 00:15 Thursday morning.
    deps = upcoming_departures(feed, "S2", local(2026, 10, 15, 0, 5), {}, TZ)
    assert [d.trip_id for d in deps] == ["T_LATE"]


def test_late_night_shows_tomorrows_first_bus(feed):
    deps = upcoming_departures(feed, "S1", local(2026, 10, 13, 23, 30), {}, TZ, window=timedelta(hours=9))
    assert [d.trip_id for d in deps][:1] == ["T1"]
    assert deps[0].scheduled.astimezone(TZ).day == 14


def test_departed_buses_drop_off(feed):
    deps = upcoming_departures(feed, "S2", local(2026, 10, 14, 8, 6), {}, TZ)
    assert "T1" not in [d.trip_id for d in deps]
