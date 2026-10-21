"""Goguyy Maps live-data backend.

Public, no-key integrations:
- NASA EONET v3 open environmental events (stored in SQLite, refreshed daily)
- NASA GIBS WMTS service availability check (imagery tiles are served directly to the map)

This backend does not claim to download raw satellite telemetry or create live video.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
DB_PATH = Path(os.getenv("GOGUYY_DB_PATH", str(ROOT / "data" / "goguyy.sqlite3")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
UTC = timezone.utc
sync_lock = threading.Lock()
scheduler_stop = threading.Event()
scheduler_thread: threading.Thread | None = None

app = FastAPI(title="Goguyy Maps Live Data API", version="1.0.0", description="Public-source satellite imagery and environmental event integration")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect_db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT, observed_at TEXT,
            lat REAL, lon REAL, source_url TEXT, raw_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
            finished_at TEXT, status TEXT NOT NULL, events_upserted INTEGER DEFAULT 0,
            gibs_status TEXT, error TEXT
        )""")


def http_json(url: str, timeout: int = 35) -> Any:
    req = urllib.request.Request(url, headers={
        "User-Agent": "GoguyyMaps/1.0 (public-data integration; contact configured by operator)",
        "Accept": "application/json"
    })
    with urllib.request.urlopen(req, timeout=timeout) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"HTTP {response.status} from data provider")
        return json.loads(response.read().decode("utf-8"))


def http_text(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "GoguyyMaps/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read(512 * 1024).decode("utf-8", errors="replace")


def coordinates_from_geometry(geometries: list[dict[str, Any]]) -> tuple[float | None, float | None, str | None]:
    """Return a representative point for display; source geometry remains in raw_json."""
    if not geometries:
        return None, None, None
    geom = geometries[-1]
    observed = geom.get("date")
    shape = geom.get("type")
    coords = geom.get("coordinates") or []
    point: list[float] | None = None
    try:
        if shape == "Point" and len(coords) >= 2:
            point = coords
        elif shape == "Polygon" and coords and coords[0]:
            ring = coords[0]
            point = [sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)]
        elif shape == "MultiPoint" and coords and len(coords[0]) >= 2:
            point = coords[0]
        elif shape == "MultiPolygon" and coords and coords[0] and coords[0][0]:
            ring = coords[0][0]
            point = [sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)]
    except (TypeError, ValueError, IndexError):
        point = None
    if point is None or len(point) < 2:
        return None, None, observed
    lon, lat = float(point[0]), float(point[1])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None, observed
    return lat, lon, observed


def run_ingestion() -> dict[str, Any]:
    """Fetch real public data, store it, and record provider outcomes."""
    if not sync_lock.acquire(blocking=False):
        return {"status": "already_running", "events_upserted": 0}
    started = now_iso()
    run_id = None
    try:
        with connect_db() as db:
            cur = db.execute("INSERT INTO sync_runs(started_at,status) VALUES(?,?)", (started, "running"))
            run_id = cur.lastrowid
        event_data = http_json("https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=500")
        items = event_data.get("events", []) if isinstance(event_data, dict) else []
        fetched = now_iso()
        count = 0
        with connect_db() as db:
            for event in items:
                event_id = str(event.get("id") or "").strip()
                if not event_id:
                    continue
                cats = event.get("categories") or []
                category = ", ".join(str(c.get("title", "")) for c in cats if c.get("title")) or "Environmental event"
                lat, lon, observed = coordinates_from_geometry(event.get("geometry") or [])
                sources = event.get("sources") or []
                source_url = event.get("link") or (sources[0].get("url") if sources else None) or "https://eonet.gsfc.nasa.gov/"
                db.execute("""INSERT INTO events(id,title,category,observed_at,lat,lon,source_url,raw_json,fetched_at)
                    VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,category=excluded.category,observed_at=excluded.observed_at,
                    lat=excluded.lat,lon=excluded.lon,source_url=excluded.source_url,
                    raw_json=excluded.raw_json,fetched_at=excluded.fetched_at""",
                    (event_id, str(event.get("title") or "Untitled event"), category, observed, lat, lon, source_url,
                     json.dumps(event, separators=(",", ":")), fetched))
                count += 1
        # GIBS is a public imagery service. Check that its public WMTS capabilities endpoint responds;
        # actual map tiles are fetched directly by the browser and aren't copied into our database.
        gibs_ok = False
        gibs_detail = "unavailable"
        try:
            body = http_text("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/wmts.cgi?SERVICE=WMTS&REQUEST=GetCapabilities", timeout=25)
            gibs_ok = "Capabilities" in body or "Layer" in body
            gibs_detail = "reachable" if gibs_ok else "unexpected response"
        except Exception as exc:
            gibs_detail = f"check failed: {type(exc).__name__}"
        finished = now_iso()
        with connect_db() as db:
            db.execute("UPDATE sync_runs SET finished_at=?,status=?,events_upserted=?,gibs_status=? WHERE id=?",
                       (finished, "success", count, gibs_detail, run_id))
            # Keep the most recent 100 successful/failed runs for diagnostics.
            db.execute("DELETE FROM sync_runs WHERE id NOT IN (SELECT id FROM sync_runs ORDER BY id DESC LIMIT 100)")
        return {"status": "success", "started_at": started, "finished_at": finished,
                "events_upserted": count, "gibs_status": gibs_detail, "gibs_reachable": gibs_ok}
    except Exception as exc:
        finished = now_iso()
        if run_id is not None:
            with connect_db() as db:
                db.execute("UPDATE sync_runs SET finished_at=?,status=?,error=? WHERE id=?",
                           (finished, "error", f"{type(exc).__name__}: {exc}"[:1000], run_id))
        return {"status": "error", "started_at": started, "finished_at": finished,
                "events_upserted": 0, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        sync_lock.release()


def daily_scheduler_loop() -> None:
    """Small dependency-free UTC scheduler; runs once daily at 03:15 while the server is alive."""
    while not scheduler_stop.is_set():
        now = datetime.now(UTC)
        next_run = now.replace(hour=3, minute=15, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        if scheduler_stop.wait(max(1.0, (next_run - now).total_seconds())):
            return
        run_ingestion()


@app.on_event("startup")
def startup() -> None:
    global scheduler_thread
    init_db()
    scheduler_stop.clear()
    scheduler_thread = threading.Thread(target=daily_scheduler_loop, name="goguyy-daily-sync", daemon=True)
    scheduler_thread.start()
    # First sync on startup, then daily scheduled syncs. Provider failures are recorded, not fabricated.
    threading.Thread(target=run_ingestion, name="goguyy-initial-sync", daemon=True).start()


@app.on_event("shutdown")
def shutdown() -> None:
    scheduler_stop.set()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "goguyy-maps", "time": now_iso()}


@app.get("/api/status")
def status() -> dict[str, Any]:
    with connect_db() as db:
        last = db.execute("SELECT * FROM sync_runs WHERE status='success' ORDER BY id DESC LIMIT 1").fetchone()
        recent = db.execute("SELECT started_at,finished_at,status,error FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
        total = db.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
    return {
        "service": "Goguyy Maps Live Data", "schedule": "daily at 03:15 UTC",
        "last_success_at": last["finished_at"] if last else None,
        "last_status": recent["status"] if recent else "not_run",
        "last_error": recent["error"] if recent and recent["error"] else None,
        "stored_event_count": total,
        "sources": {
            "nasa_gibs": {"type": "public WMTS imagery tiles", "api_key_required": False,
                          "note": "Fetched by the browser; acquisition date and coverage vary by layer."},
            "nasa_eonet": {"type": "public open environmental events API", "api_key_required": False,
                           "note": "Events are observations/curated events, not continuous satellite tracking."},
            "copernicus_sentinel": {"enabled": False, "note": "Optional future connector; many catalogue downloads and processing APIs require account/token credentials."}
        }
    }


@app.get("/api/events")
def events(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    with connect_db() as db:
        rows = db.execute("SELECT id,title,category,observed_at,lat,lon,source_url,fetched_at FROM events ORDER BY COALESCE(observed_at,'') DESC LIMIT ?", (limit,)).fetchall()
    return {"count": len(rows), "events": [dict(r) for r in rows]}


@app.post("/api/ingest/run")
def manual_ingest() -> dict[str, Any]:
    result = run_ingestion()
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.get("error", "Public data source failed"))
    if result.get("status") == "already_running":
        raise HTTPException(status_code=409, detail="A data sync is already running")
    return result


@app.get("/api/ingest/history")
def ingest_history(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    with connect_db() as db:
        rows = db.execute("SELECT id,started_at,finished_at,status,events_upserted,gibs_status,error FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"runs": [dict(r) for r in rows]}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html", media_type="text/html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
