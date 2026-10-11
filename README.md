# Res Bus: HSR departures for McMaster residences

Pick your residence, see the next buses at the stops you can actually walk to, with live predictions from Hamilton Street Railway's realtime feed. One screen, no map hunting, no ads.

> Built because the existing transit apps make you search for a stop every single time when all I want to know is "when's the next bus outside my res?"

## How it works

```
GTFS static (.zip, daily)            GTFS-Realtime TripUpdates (.pb, every 30s)
        │                                         │
        ▼                                         ▼
  load_static()  ── only keeps          parse_trip_updates()
  stop_times near residences            (cached, survives outages)
        │                                         │
        └──────────────► upcoming_departures() ◄──┘
                         schedule + live delay
                                  │
                         FastAPI  /api/residences/{id}
                                  │
                         static/  phone-first web page (auto-refresh)
```

Some details that took actual thought:

- **Memory:** HSR's `stop_times.txt` is ~70 MB. The loader streams it and keeps only rows for stops within walking distance of a residence.
- **Service days:** GTFS times can go past `24:00:00` (a "25:10" bus belongs to yesterday's schedule), and times are measured from *noon minus 12 h* so they stay correct on daylight-saving days. Both are handled and tested.
- **Holidays:** `calendar_dates.txt` exceptions (e.g. Thanksgiving running a Saturday schedule) are applied.
- **Realtime merging:** exact stop prediction > delay propagated from the bus's most recent stop > trip-level delay > schedule. Cancelled trips and skipped stops are flagged instead of silently showing a bus that won't come.
- **Graceful degradation:** if the realtime feed is down, the app keeps serving the last good copy, then falls back to the schedule and says so.

## Run it

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://localhost:8000. The first start downloads the HSR schedule (~13 MB), cached for 24 h in `.cache/`.

To use it from your phone on the same Wi-Fi: `uvicorn app.main:app --host 0.0.0.0` and visit your computer's IP.

### Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests use a small hand-built GTFS feed and protobuf realtime messages (`tests/fixtures.py`), so they run offline.

### Configuration

All settings live in `app/config.py` and can be overridden with environment variables, e.g. `HSR_NEARBY_RADIUS_M=700`. Residence locations are in `app/data/residences.json`.

## Roadmap

- [ ] Verify residence coordinates (currently approximate)
- [ ] Group by direction ("to Westdale" / "to downtown") instead of by stop
- [ ] Service alerts feed (detours, stop closures)
- [ ] Live bus positions on a small map (VehiclePositions feed)
- [ ] Re-download the schedule automatically while the server is running
- [ ] Deploy (Fly.io / Render) + installable PWA

## Data

Schedule and realtime data: [City of Hamilton Open Data](https://www.hamilton.ca/city-council/data-maps/open-data), used under its open data licence. This project is not affiliated with HSR, the City of Hamilton, or McMaster University.
