"""Load the HSR static GTFS feed (stops, routes, trips, schedule).

The full stop_times.txt is ~70 MB unzipped, so we stream it and only keep
rows for the stops we actually care about (the ones near residences).
"""

from __future__ import annotations

import csv
import io
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterator

import httpx


@dataclass(frozen=True)
class Stop:
    stop_id: str
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Route:
    route_id: str
    short_name: str
    long_name: str
    color: str
    text_color: str


@dataclass(frozen=True)
class Trip:
    trip_id: str
    route_id: str
    service_id: str
    headsign: str


@dataclass(frozen=True)
class StopTime:
    trip_id: str
    stop_id: str
    stop_sequence: int
    departure_secs: int  # seconds after service-day "midnight"; can exceed 24h


@dataclass
class StaticFeed:
    stops: dict[str, Stop]
    routes: dict[str, Route]
    trips: dict[str, Trip]
    stop_times_by_stop: dict[str, list[StopTime]]
    # service_id -> (weekday flags Mon..Sun, start date, end date)
    calendar: dict[str, tuple[tuple[bool, ...], date, date]] = field(default_factory=dict)
    # (service_id, date) -> True if added, False if removed
    calendar_dates: dict[tuple[str, date], bool] = field(default_factory=dict)

    def active_service_ids(self, d: date) -> set[str]:
        active = {
            sid
            for sid, (days, start, end) in self.calendar.items()
            if start <= d <= end and days[d.weekday()]
        }
        for (sid, cd), added in self.calendar_dates.items():
            if cd != d:
                continue
            if added:
                active.add(sid)
            else:
                active.discard(sid)
        return active

    def date_range(self) -> tuple[date | None, date | None]:
        dates = [s for _, s, _ in self.calendar.values()] + [e for _, _, e in self.calendar.values()]
        dates += [d for (_, d) in self.calendar_dates]
        return (min(dates), max(dates)) if dates else (None, None)

    def covers(self, d: date) -> bool:
        return bool(self.active_service_ids(d))


def parse_gtfs_time(value: str) -> int:
    """'25:10:00' -> seconds. GTFS allows hours >= 24 for after-midnight trips."""
    h, m, s = value.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def _parse_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y%m%d").date()


def _rows(zf: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    if name not in zf.namelist():
        return iter(())
    # utf-8-sig strips the BOM some agencies put at the start of files
    return csv.DictReader(io.TextIOWrapper(zf.open(name), encoding="utf-8-sig"))


def download_static(url: str, dest: Path, max_age_hours: int) -> Path:
    """Download the static feed unless a fresh-enough copy is cached.

    `url` may also be a local file path (handy for tests and offline demos).
    """
    if not url.startswith(("http://", "https://")):
        return Path(url)
    if dest.exists() and (time.time() - dest.stat().st_mtime) < max_age_hours * 3600:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with httpx.stream("GET", url, timeout=60, follow_redirects=True) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    tmp.replace(dest)
    return dest


def load_static(zip_path: Path, keep_stop: Callable[[Stop], bool]) -> StaticFeed:
    """Parse a GTFS zip. Only stop_times for stops where keep_stop(stop) is True are kept."""
    with zipfile.ZipFile(zip_path) as zf:
        stops = {
            r["stop_id"]: Stop(r["stop_id"], r["stop_name"], float(r["stop_lat"]), float(r["stop_lon"]))
            for r in _rows(zf, "stops.txt")
            if r.get("stop_lat") and r.get("stop_lon")
        }
        wanted = {sid for sid, s in stops.items() if keep_stop(s)}

        routes = {
            r["route_id"]: Route(
                r["route_id"],
                r.get("route_short_name", "").strip(),
                r.get("route_long_name", "").strip(),
                (r.get("route_color") or "1F4E79").strip(),
                (r.get("route_text_color") or "FFFFFF").strip(),
            )
            for r in _rows(zf, "routes.txt")
        }

        trips = {
            r["trip_id"]: Trip(r["trip_id"], r["route_id"], r["service_id"], r.get("trip_headsign", "").strip())
            for r in _rows(zf, "trips.txt")
        }

        by_stop: dict[str, list[StopTime]] = defaultdict(list)
        for r in _rows(zf, "stop_times.txt"):
            sid = r["stop_id"]
            if sid not in wanted:
                continue
            t = r.get("departure_time") or r.get("arrival_time")
            if not t:
                continue  # untimed stop; skip for now
            by_stop[sid].append(StopTime(r["trip_id"], sid, int(r["stop_sequence"]), parse_gtfs_time(t)))
        for lst in by_stop.values():
            lst.sort(key=lambda st: st.departure_secs)

        day_cols = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        calendar = {
            r["service_id"]: (
                tuple(r[c] == "1" for c in day_cols),
                _parse_date(r["start_date"]),
                _parse_date(r["end_date"]),
            )
            for r in _rows(zf, "calendar.txt")
        }
        calendar_dates = {
            (r["service_id"], _parse_date(r["date"])): r["exception_type"].strip() == "1"
            for r in _rows(zf, "calendar_dates.txt")
        }

    return StaticFeed(stops, routes, trips, dict(by_stop), calendar, calendar_dates)
