from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .geo import haversine_m
from .gtfs_static import Stop


@dataclass(frozen=True)
class Residence:
    id: str
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class NearbyStop:
    stop: Stop
    distance_m: float


def load_residences(path: Path) -> list[Residence]:
    raw = json.loads(path.read_text())
    return [Residence(r["id"], r["name"], r["lat"], r["lon"]) for r in raw["residences"]]


def nearby_stops(res: Residence, stops: dict[str, Stop], radius_m: float, limit: int) -> list[NearbyStop]:
    found = [
        NearbyStop(s, d)
        for s in stops.values()
        if (d := haversine_m(res.lat, res.lon, s.lat, s.lon)) <= radius_m
    ]
    found.sort(key=lambda n: n.distance_m)
    return found[:limit]


def is_near_any(stop: Stop, residences: list[Residence], radius_m: float) -> bool:
    return any(haversine_m(r.lat, r.lon, stop.lat, stop.lon) <= radius_m for r in residences)
