# Res Bus: HSR trip planner for McMaster residences

Pick your residence and where you're going. Res Bus tells you **when to leave**, which stop to walk to, which bus to take, and when you'll arrive, using live predictions from Hamilton Street Railway's realtime feed. There's also a **Next buses** board for the stops around your res.

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

- **Trip planning:** for every bus leaving a stop near your res, the planner rides forward along that trip looking for a stop near your destination. *Leave by* = live departure − walk time − 2 min buffer; *arrive* = live arrival + walk from the stop. Options that make you leave earlier *and* arrive later than another option are dropped, and "arrive by" mode lists the latest you can get away with first.
- **Nearby-stop matching:** destinations include stops within 250 m, so it will happily tell you to get off at MacNab Terminal and walk a minute to King at James if that bus is faster.

- **Memory:** HSR's `stop_times.txt` is ~70 MB. The loader streams it twice: once to find trips that pass near a residence, then to keep only those trips' stops (with slotted dataclasses). Untimed stops are interpolated between timepoints.
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

### On your phone

It's a PWA: open the site in Chrome on Android, then tap **⋮ → Add to Home screen** (Safari on iOS: **Share → Add to Home Screen**) for a full-screen app with its own icon.

The phone needs a URL it can reach:

- **Quick test:** run `cloudflared tunnel --url http://localhost:8000` on your laptop and open the `https://….trycloudflare.com` link it prints. Works on campus Wi-Fi, which often blocks device-to-device traffic.
- **Always on:** push to GitHub, then on [Render](https://render.com) choose *New → Blueprint* and pick this repo (`render.yaml` is already set up).
- **Real APK:** once it's hosted, paste the URL into [PWABuilder](https://www.pwabuilder.com) to generate an Android package.

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
- [x] Trip planner: leave-by time, live ETA, arrive-by mode (direct buses)
- [ ] Transfers (RAPTOR routing)
- [ ] Search by address or place name, not just stop name
- [ ] Service alerts feed (detours, stop closures)
- [ ] Live bus positions on a small map (VehiclePositions feed)
- [ ] Re-download the schedule automatically while the server is running
- [x] Installable PWA (manifest, icons, offline app shell)
- [ ] Deploy to Render and publish an APK via PWABuilder

## Data

Schedule and realtime data: [City of Hamilton Open Data](https://www.hamilton.ca/city-council/data-maps/open-data), used under its open data licence. This project is not affiliated with HSR, the City of Hamilton, or McMaster University.
