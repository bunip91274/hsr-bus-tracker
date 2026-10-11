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
        feed=feed,
        rt=RealtimeCache(config.TRIP_UPDATES_URL, config.REALTIME_TTL_SECONDS),
        http=httpx.AsyncClient(follow_redirects=True),
    )
    yield
    await state["http"].aclose()


app = FastAPI(title="HSR Bus Tracker", lifespan=lifespan)


def _dep_json(d: Departure, now: datetime) -> dict:
    return {
        "route": d.route_short_name,
        "color": f"#{d.route_color}",
        "text_color": f"#{d.route_text_color}",
        "headsign": d.headsign,
        "minutes": max(0, int((d.best - now).total_seconds() // 60)),
        "time": d.best.astimezone(config.TIMEZONE).strftime("%I:%M %p").lstrip("0"),
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


# Serve the frontend last so /api routes win.
app.mount("/", StaticFiles(directory=config.ROOT / "static", html=True), name="static")
