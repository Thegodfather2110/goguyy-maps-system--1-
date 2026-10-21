# Goguyy Maps — live public-data edition

This project preserves the uploaded Goguyy Maps HTML app and adds a small backend for real public data, SQLite storage, a daily scheduled sync, and a NASA satellite imagery layer.

## What's included

- **Original map features retained:** Explore, Route, Editor, Import, existing map layers and locally stored map data.
- **NASA GIBS VIIRS True Color imagery layer:** choose **NASA Satellite (VIIRS)** in the map layer picker. The browser loads real NASA imagery tiles for a recent selected date (default: two days ago), with attribution. This is satellite imagery, not live video; data latency, cloud cover, tile coverage and zoom limits vary.
- **NASA EONET events:** the backend retrieves open environmental-event records from NASA's public EONET API and stores them in SQLite. Click **Show NASA events** to display records with coordinates. The records are environmental events, not a complete live feed of all satellite detections.
- **Scheduled ingestion:** backend sync runs daily at **03:15 UTC**, and the map panel offers a manual **Run data sync** action.
- **Freshness and honest status:** status panel shows backend connectivity and last successful ingestion time. A failed/unavailable source is reported as unavailable; the UI does not generate fake observations.
- **Expandable provider design:** Copernicus Sentinel data is documented as an optional connector. Some catalogue download/processing APIs require an account and OAuth token; credentials are deliberately not invented or embedded.

## Optional Google Maps Platform connection

The **Google Maps** section in the left navigation lets you paste a Google Maps Platform browser API key and open Google Maps as an alternate map view. The key is stored only in that browser's `localStorage`; it is not sent to the Goguyy backend. Use **Disconnect** to remove the saved key.

Before connecting:

1. In the [Google Maps Platform credentials page](https://console.cloud.google.com/google/maps-apis/credentials), select/create a Google Cloud project.
2. Enable **Maps JavaScript API** and configure billing for the project as required by Google.
3. Create a browser API key and apply **HTTP referrer restrictions** for your site. Restrict the key to only the APIs you need.
4. Serve Goguyy over `http://localhost:8000` for local development, or HTTPS for deployment. Avoid opening the HTML as a `file://` URL because browser referrer restrictions may not work reliably there.
5. Paste the key in **Google Maps** → **Connect & show Google Maps**.

Google Maps is deliberately a separate alternate map view, not a Google tile layer inside Leaflet. Google map labels, controls, markers, imagery and usage follow Google Maps Platform terms. The current alternate view mirrors the current map center/zoom and shows up to 250 Goguyy place markers in the visible area. Existing Goguyy editing, routing and other Leaflet-only tools require switching back by opening the Google Maps section and choosing **Disconnect**. Google Maps Platform can incur charges; the key is visible to the browser by design, so referrer and API restrictions are important. See [Google's key setup guide](https://developers.google.com/maps/documentation/javascript/get-api-key) and [security best practices](https://developers.google.com/maps/api-security-best-practices).

## Run locally on Windows

1. Install Python 3.11+ (3.12 recommended).
2. Open Command Prompt or PowerShell in this folder.
3. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

4. Install packages and start the server:

   ```powershell
   pip install -r requirements.txt
   uvicorn backend:app --host 127.0.0.1 --port 8000
   ```

5. Open <http://127.0.0.1:8000>. Keep the terminal/server running for scheduled updates; the first sync starts automatically after server startup. On first run, press **Run data sync** in the live-data panel to load current public event data immediately.

If PowerShell blocks environment activation, you can skip activation and run `.venv\Scripts\python.exe -m pip install -r requirements.txt` then `.venv\Scripts\python.exe -m uvicorn backend:app --host 127.0.0.1 --port 8000`.

## Run with Docker

```bash
docker compose up --build -d
```

Then open <http://localhost:8000>. SQLite persists in the `goguyy_data` Docker volume.

## API endpoints

- `GET /api/health` — liveness check
- `GET /api/status` — sync status, schedule, data-source details and event count
- `GET /api/events?limit=100` — cached NASA EONET events
- `POST /api/ingest/run` — run ingestion now
- `GET /api/ingest/history` — recent ingestion runs

## Sources and constraints

- NASA GIBS public map imagery: <https://nasa-gibs.github.io/gibs-api-docs/>. The frontend requests a dated WMTS imagery layer directly; the backend checks the service during each ingestion run. The default date is not a guarantee that every tile has data.
- NASA EONET public events: <https://eonet.gsfc.nasa.gov/docs/v3>. This is a real event API; events can be missing coordinates, delayed, revised, or removed as their status changes.
- Copernicus Data Space APIs: <https://documentation.dataspace.copernicus.eu/APIs.html>. The project does not claim anonymous access to protected processing/download APIs. Credentials, if added later, should be stored as server-side environment secrets and never in browser code.
- Basemap, geocoding, routing and Overpass features in the original HTML retain their own provider policies, availability and rate limits. For public deployment, review tile/service terms, add contact details where required, and use dedicated or commercial services if traffic grows.
- The scheduled job runs only while the backend process/container is running. For production, deploy on an always-on host, configure backups/monitoring, and set HTTPS and access controls before exposing management endpoints publicly.

## Architecture

`Browser map (Leaflet) → FastAPI → SQLite`  
`Daily scheduler → NASA EONET public API → normalized event records`  
`Browser → NASA GIBS WMTS → satellite imagery tiles`

No manual API key is needed for the included NASA GIBS imagery or EONET event connection. This does not mean every satellite is directly accessible: satellites have separate owners, products, access rules and update schedules.
