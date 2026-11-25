from datetime import datetime, timedelta

import pytest

from app.config import TIMEZONE as TZ
from app.gtfs_static import interpolate_times, load_static
from app.planner import plan_trips
from app.realtime import parse_trip_updates
from app.residences import Residence, nearby_stops

from .fixtures import make_trip_updates, write_gtfs_zip


@pytest.fixture
def feed(tmp_path):
    # Like the real app: only stops near campus count as "near a residence".
    return load_static(write_gtfs_zip(tmp_path / "gtfs.zip"), keep_stop=lambda s: s.stop_id in {"S1", "S2"})


@pytest.fixture
def origin(feed):
    return nearby_stops(Residence("pgcll", "PGCLL", 43.2603, -79.9228), feed.stops, 500, 50)


def at(h, m, day=14):  # Oct 14 2026 is a Wednesday
    return datetime(2026, 10, day, h, m, tzinfo=TZ)


def plan(feed, origin, now, to="FAR", rt=None, **kw):
    return plan_trips(feed, origin, to, now, rt or {}, TZ, **kw)


def test_interpolates_untimed_stops():
    assert interpolate_times([None, 0, None, None, 300, None]) == [None, 0, 100, 200, 300, None]


def test_trip_sequences_include_downstream_stops(feed):
    assert [st.stop_id for st in feed.stop_times_by_trip["T1"]] == ["S1", "S2", "FAR"]
    assert "FAR" in feed.reachable_stop_ids()


def test_options_sorted_by_arrival(feed, origin):
    opts = plan(feed, origin, at(7, 50))
    assert [o.trip_id for o in opts] == ["T1", "T3", "T2"]
    t1 = opts[0]
    # Two ways to catch T1 (S1 at 8:00 or S2 at 8:05); same arrival, so take the one you can leave later for.
    from app.planner import walk_time

    leave = {
        ns.stop.stop_id: at(8, 0 if ns.stop.stop_id == "S1" else 5) - walk_time(ns.distance_m)
        for ns in origin
    }
    assert t1.board_stop.stop_id == max(leave, key=leave.get)
    assert t1.leave_by == t1.depart - t1.walk_to_stop - timedelta(minutes=2)
    assert t1.arrive.astimezone(TZ).strftime("%H:%M") == "08:30"
    assert t1.num_stops == (2 if t1.board_stop.stop_id == "S1" else 1)


def test_alight_at_nearby_stop_and_walk(feed, origin):
    opts = plan(feed, origin, at(7, 50), to="FAR2")
    assert opts[0].alight_stop.stop_id == "FAR"
    assert opts[0].walk_from_stop > timedelta(0)
    assert opts[0].arrive > opts[0].arrive_stop


def test_missed_bus_is_dropped(feed, origin):
    # At 7:59 you can't walk to S1 for the 8:00 anymore (and the 8:05 from S2 needs ~6 min).
    assert [o.trip_id for o in plan(feed, origin, at(7, 59))] == ["T3", "T2"]


def test_arrive_by_lists_latest_leave_first(feed, origin):
    opts = plan(feed, origin, at(7, 50), arrive_by=at(8, 40))
    assert [o.trip_id for o in opts] == ["T3", "T1"]


def test_live_delay_can_make_an_option_pointless(feed, origin):
    rt = parse_trip_updates(make_trip_updates([{"trip_id": "T1", "stops": [{"seq": 3, "delay": 900}]}]))
    opts = plan(feed, origin, at(7, 50), rt=rt)
    # T1 now arrives 8:45 but you'd leave at ~7:57; T2 also arrives 8:45 and lets you leave at ~8:17.
    assert [o.trip_id for o in opts] == ["T3", "T2"]


def test_canceled_trip_not_offered(feed, origin):
    rt = parse_trip_updates(make_trip_updates([{"trip_id": "T3", "canceled": True}]))
    assert [o.trip_id for o in plan(feed, origin, at(7, 50), rt=rt)] == ["T1", "T2"]


def test_unreachable_destination(feed, origin):
    assert plan(feed, origin, at(7, 50), to="S1") == []
