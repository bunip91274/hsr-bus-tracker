"""FastAPI app: residence picker -> nearby stops -> next buses (live when available)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from . import config
from .departures import Departure, upcoming_departures
from .gtfs_static import download_static, load_static
from .planner import TripOption, plan_trips
from .realtime import RealtimeCache
from .residences import is_near_any, load_residences, nearby_stops

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("hsr")

state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    residences = load_residences(config.RESIDENCES_FILE)
    zip_path = download_static(
        config.STATIC_GTFS_URL, config.CACHE_DIR / "hsr_gtfs.zip", config.STATIC_MAX_AGE_HOURS
    )
    feed = load_static(zip_path, keep_stop=lambda s: is_near_any(s, residences, config.NEARBY_RADIUS_M))

    today = datetime.now(config.TIMEZONE).date()
    start, end = feed.date_range()
    log.info("Static feed covers %s to %s", start, end)
    if not feed.covers(today):
        log.warning("Static feed has no service for today (%s) - HSR may have published a new one.", today)

    state.update(
        residences={r.id: r for r in residences},
        nearby={
            r.id: nearby_stops(r, feed.stops, config.NEARBY_RADIUS_M, config.MAX_STOPS_PER_RESIDENCE)
            for r in residences
        },
        # The planner considers every stop within walking range, not just the few shown on the board.
        origin_stops={r.id: nearby_stops(r, feed.stops, config.NEARBY_RADIUS_M, 50) for r in residences},
        destinations=_build_destination_index(feed),
        feed=feed,
        rt=RealtimeCache(config.TRIP_UPDATES_URL, config.REALTIME_TTL_SECONDS),
        http=httpx.AsyncClient(follow_redirects=True),
    )
    yield
    await state["http"].aclose()


app = FastAPI(title="HSR Bus Tracker", lifespan=lifespan)


def _build_destination_index(feed) -> list[dict]:
    """One entry per stop name (each name usually has a stop on both sides of the street)."""
    by_name: dict[str, str] = {}
    for sid in sorted(feed.reachable_stop_ids()):
        stop = feed.stops.get(sid)
        if stop:
            by_name.setdefault(stop.name, sid)
    return sorted(({"name": n, "stop_id": s} for n, s in by_name.items()), key=lambda d: d["name"])


def _fmt_time(dt: datetime) -> str:
    return dt.astimezone(config.TIMEZONE).strftime("%I:%M %p").lstrip("0")


def _mins_until(dt: datetime, now: datetime) -> int:
    return int((dt - now).total_seconds() // 60)


def _option_json(o: TripOption, now: datetime) -> dict:
    return {
        "route": o.route_short_name,
        "color": f"#{o.route_color}",
        "text_color": f"#{o.route_text_color}",
        "headsign": o.headsign,
        "leave_by": _fmt_time(o.leave_by),
        "leave_in_min": max(0, _mins_until(o.leave_by, now)),
        "board_stop": o.board_stop.name,
        "walk_to_min": max(1, round(o.walk_to_stop.total_seconds() / 60)),
        "depart": _fmt_time(o.depart),
        "alight_stop": o.alight_stop.name,
        "num_stops": o.num_stops,
        "ride_min": round((o.arrive_stop - o.depart).total_seconds() / 60),
        "walk_from_min": round(o.walk_from_stop.total_seconds() / 60),
        "arrive": _fmt_time(o.arrive),
        "live": o.live,
    }


def _dep_json(d: Departure, now: datetime) -> dict:
    return {
        "route": d.route_short_name,
        "color": f"#{d.route_color}",
        "text_color": f"#{d.route_text_color}",
        "headsign": d.headsign,
        "minutes": max(0, int((d.best - now).total_seconds() // 60)),
        "time": _fmt_time(d.best),
        "live": d.is_live,
        "delay_min": None if d.delay_seconds is None else round(d.delay_seconds / 60),
        "canceled": d.canceled,
    }


@app.get("/api/health")
async def health():
    feed, rt = state["feed"], state["rt"]
    start, end = feed.date_range()
    return {
        "static_feed": {"start": str(start), "end": str(end),
                        "covers_today": feed.covers(datetime.now(config.TIMEZONE).date())},
        "realtime": {"age_seconds": rt.age_seconds, "last_error": rt.last_error},
    }


@app.get("/api/residences")
async def list_residences():
    return [{"id": r.id, "name": r.name} for r in state["residences"].values()]


@app.get("/api/residences/{res_id}")
async def residence_departures(res_id: str):
    res = state["residences"].get(res_id)
    if res is None:
        raise HTTPException(404, f"Unknown residence '{res_id}'")

    rt_cache: RealtimeCache = state["rt"]
    realtime = await rt_cache.get(state["http"])
    now = datetime.now(timezone.utc)

    stops = []
    for ns in state["nearby"][res_id]:
        deps = upcoming_departures(
            state["feed"], ns.stop.stop_id, now, realtime, config.TIMEZONE,
            window=timedelta(minutes=config.WINDOW_MINUTES), limit=config.DEPARTURES_PER_STOP,
        )
        stops.append({
            "stop_id": ns.stop.stop_id,
            "name": ns.stop.name,
            "distance_m": round(ns.distance_m),
            "walk_min": max(1, round(ns.distance_m / 80)),  # ~80 m/min walking pace
            "departures": [_dep_json(d, now) for d in deps],
        })

    return {
        "residence": {"id": res.id, "name": res.name},
        "stops": stops,
        "realtime": {"age_seconds": rt_cache.age_seconds, "ok": rt_cache.last_error is None},
        "generated_at": now.isoformat(),
    }


@app.get("/api/stops/search")
async def search_stops(q: str = "", limit: int = 12):
    """Destinations you can reach on one bus, matched by name ('lime mall' finds 'Limeridge Mall ...')."""
    words = q.lower().split()
    if not words:
        return []
    hits = [d for d in state["destinations"] if all(w in d["name"].lower() for w in words)]
    first = words[0]
    hits.sort(key=lambda d: (not d["name"].lower().startswith(first), d["name"]))
    return hits[:limit]


def _parse_arrive_by(value: str, now: datetime) -> datetime:
    try:
        hh, mm = (int(x) for x in value.split(":"))
        local_now = now.astimezone(config.TIMEZONE)
        target = local_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except ValueError:
        raise HTTPException(400, "arrive_by must look like 14:30")
    if target < local_now:
        target += timedelta(days=1)  # "arrive by 8:30" said at night means tomorrow morning
    return target


@app.get("/api/plan")
async def plan(res: str, to: str, arrive_by: str | None = None):
    if res not in state["residences"]:
        raise HTTPException(404, f"Unknown residence '{res}'")
    feed = state["feed"]
    if to not in feed.stops:
        raise HTTPException(404, f"Unknown stop '{to}'")

    rt_cache: RealtimeCache = state["rt"]
    realtime = await rt_cache.get(state["http"])
    now = datetime.now(timezone.utc)
    deadline = _parse_arrive_by(arrive_by, now) if arrive_by else None

    options = plan_trips(feed, state["origin_stops"][res], to, now, realtime, config.TIMEZONE, arrive_by=deadline)

    message = None
    if not options:
        message = (
            "No direct bus gets you there in time." if deadline
            else "No direct bus in the next 3 hours. This trip probably needs a transfer (coming soon)."
        )
    return {
        "destination": feed.stops[to].name,
        "options": [_option_json(o, now) for o in options],
        "message": message,
        "realtime": {"age_seconds": rt_cache.age_seconds, "ok": rt_cache.last_error is None},
        "generated_at": now.isoformat(),
    }


# Serve the frontend last so /api routes win.
app.mount("/", StaticFiles(directory=config.ROOT / "static", html=True), name="static")
