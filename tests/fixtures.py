"""Tiny hand-made GTFS + GTFS-RT feeds so tests don't depend on the city's servers."""

from __future__ import annotations

import zipfile
from pathlib import Path

from google.transit import gtfs_realtime_pb2

FILES = {
    "stops.txt": (
        "stop_id,stop_name,stop_lat,stop_lon\n"
        "S1,Main at Emerson,43.2600,-79.9230\n"
        "S2,McMaster University Terminal,43.2616,-79.9180\n"
        "FAR,Downtown Gore,43.2557,-79.8690\n"
        "FAR2,Gore Park EB,43.2559,-79.8686\n"
    ),
    "routes.txt": (
        "route_id,route_short_name,route_long_name,route_type,route_color,route_text_color\n"
        "R1,1,King,3,E31837,FFFFFF\n"
        "R51,51,University,3,0072BC,FFFFFF\n"
    ),
    "trips.txt": (
        "route_id,service_id,trip_id,trip_headsign\n"
        "R1,WKDY,T1,Eastgate\n"
        "R1,WKDY,T2,Eastgate\n"
        "R51,WKDY,T3,Downtown\n"
        "R51,WKDY,T_LATE,Downtown\n"
        "R1,SAT,T_SAT,Eastgate\n"
    ),
    "stop_times.txt": (
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        "T1,08:00:00,08:00:00,S1,1\n"
        "T1,08:05:00,08:05:00,S2,2\n"
        "T1,08:30:00,08:30:00,FAR,3\n"
        "T2,08:20:00,08:20:00,S1,1\n"
        "T2,08:25:00,08:25:00,S2,2\n"
        "T2,08:45:00,08:45:00,FAR,3\n"
        "T3,08:10:00,08:10:00,S2,1\n"
        "T3,08:35:00,08:35:00,FAR,2\n"
        "T_LATE,24:15:00,24:15:00,S2,1\n"
        "T_SAT,08:00:00,08:00:00,S1,1\n"
    ),
    "calendar.txt": (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        "WKDY,1,1,1,1,1,0,0,20260901,20261231\n"
        "SAT,0,0,0,0,0,1,0,20260901,20261231\n"
    ),
    # Thanksgiving Monday 2026: weekday service removed, Saturday service added.
    "calendar_dates.txt": (
        "service_id,date,exception_type\n"
        "WKDY,20261012,2\n"
        "SAT,20261012,1\n"
    ),
}


def write_gtfs_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in FILES.items():
            # Include a BOM on one file to make sure the loader handles it.
            zf.writestr(name, ("﻿" + body) if name == "stops.txt" else body)
    return path


def make_trip_updates(updates: list[dict]) -> bytes:
    """updates: [{trip_id, canceled?, stops: [{seq, delay?, time?, skipped?}]}]"""
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    for i, u in enumerate(updates):
        ent = msg.entity.add()
        ent.id = str(i)
        tu = ent.trip_update
        tu.trip.trip_id = u["trip_id"]
        if u.get("canceled"):
            tu.trip.schedule_relationship = gtfs_realtime_pb2.TripDescriptor.CANCELED
        for s in u.get("stops", []):
            stu = tu.stop_time_update.add()
            stu.stop_sequence = s["seq"]
            if s.get("skipped"):
                stu.schedule_relationship = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.SKIPPED
            if "delay" in s:
                stu.departure.delay = s["delay"]
            if "time" in s:
                stu.departure.time = s["time"]
    return msg.SerializeToString()
