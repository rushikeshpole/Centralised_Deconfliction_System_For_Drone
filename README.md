# Centralised Deconfliction System For Drone

A centralized UAV (drone) deconfliction and mission management system built with Flask and Flask-SocketIO. This repository implements a real-time server that monitors multiple drones, detects proximity conflicts, records and serves trajectories (historical & future), schedules missions with deconfliction checks, and exposes both REST APIs and WebSocket events for UI and programmatic clients.

Primary languages:
- Python (~49.7%)
- HTML (~44%)
- CSS (~6.3%)

---

Table of contents
- Project overview
- Features
- Architecture & data flow
- Directory / script overview
- HTTP API endpoints (summary)
- WebSocket events (summary)
- Installation & quick start
- Usage examples (curl & WebSocket)
- Configuration & deployment notes
- Development notes and TODOs
- Security & license

---

Project overview

This project provides a centralized control and deconfliction system for small fleets of drones. It:
- Collects and simulates drone state (position, velocity, battery, armed state).
- Records trajectories and serves them on demand (historical and future/planned).
- Runs a deconfliction engine to prevent mission conflicts and to detect realtime proximity issues.
- Exposes an interactive dashboard and visualization web pages (templates).
- Supports direct control commands (arm, takeoff, land, goto) via REST and WebSocket.
- Emits realtime updates (drone states and conflict alerts) to connected clients via Socket.IO.

The code includes fallbacks/dummy implementations so the UI and server can run without a real database or drone hardware. Replace dummy modules with your real `database`, `drone_controller`, `deconfliction_engine`, and `mission_executor` implementations for production operation.

---

Features

- Real-time drone state broadcasting at configurable rates (default: 2 Hz / 500 ms).
- Realtime conflict detection and alerting.
- Mission scheduling with preflight deconfliction.
- Trajectory recording, historical playback and trajectory statistics.
- RESTful API surface for monitoring, control and data retrieval.
- Socket.IO support for low-latency UI updates and control.
- Dummy implementations to enable development without hardware.

---

Architecture & data flow

Components:
- Flask web server: Serves UI templates and REST endpoints.
- Flask-SocketIO: Bi-directional, low-latency communication with clients for streaming updates and control.
- Drone Controller: Provides real/simulated drone states, command execution and trajectory recording.
- Deconfliction Engine: Validates planned missions against existing missions/trajectories; computes conflicts.
- Mission Executor: Schedules missions and coordinates their execution.
- Database module: Persisting & retrieving trajectories, missions, and conflict history.

Typical data flow:
1. Drone controller continuously updates internal drone state and appends trajectory points.
2. Background update thread reads states from the drone controller, runs realtime conflict checks and broadcasts `drone_update` to WebSocket clients.
3. Clients (dashboard, visualization, or external) receive updates; they can request history, schedule missions, or control drones.
4. Mission scheduling API calls the Deconfliction Engine to check safety; if safe, Mission Executor stores/schedules the mission.
5. Historical queries retrieve recorded trajectories from the database (or dummy storage), calculate statistics and return JSON payloads.
6. Emergency or immediate commands are executed on the Drone Controller and an immediate `drone_update` is emitted.

Sequence diagram (conceptual):
Client <--> Socket.IO <--> Flask Server <--> Drone Controller
                           \--> Deconfliction Engine
                           \--> Mission Executor
                           \--> Database

---

Repository / script overview

Note: The repo contains Python, HTML and CSS files. The following is an overview of the primary scripts/modules referenced by the main server file. If a module is not present or importable, the server contains dummy fallbacks to allow local testing.

- server (main Flask app) — Example: `app.py` or `server.py`
  - Initializes Flask + Socket.IO, global instances and background update thread.
  - Defines REST API endpoints and WebSocket handlers.
  - Starts the service and update thread on boot.
  - (The provided server file implements: initialization, update thread, endpoints, conflict detection, and socket event handlers.)

- database.py
  - Responsibilities:
    - `init_db()` — Initialize database connections / migrations.
    - `get_all_drones_status()` — Retrieve latest drone statuses (if persisted).
    - `create_mission()` — Persist mission data.
    - `get_active_missions()` — Return active missions & statuses.
    - `get_drone_trajectory(drone_id, start, end)` — Retrieve stored historical trajectory points.
    - (Optional) `get_future_trajectories(start_time, end_time)` — Return planned/future trajectories.
  - Replace dummy DB with a real DB backend (Postgres, SQLite, etc.) or an external telemetry store.

- deconfliction_engine.py
  - Responsibilities:
    - `DeconflictionEngine(safety_buffer=...)` — Evaluate mission safety.
    - `check_mission_conflict(drone_id, waypoints, start_time, end_time)` — Return whether a mission is safe and any conflict details.
  - This module is core to scheduling correctness and safety.

- drone_controller.py
  - Responsibilities:
    - `EnhancedDroneController(drone_count=...)` — Abstraction for drone fleet.
    - Provide methods: `get_drone_status()`, `get_all_status()`, `get_trajectory()`, `start_recording()`, `arm_drone()`, `disarm_drone()`, `takeoff()`, `land()`, `return_to_launch()`, `goto_position()`, `emergency_stop_all()`.
    - Maintain per-drone trajectory buffers and update simulated or real telemetry.
  - For hardware-in-the-loop integrate MAVLink/DroneKit/other SDK.

- mission_executor.py
  - Responsibilities:
    - Scheduling missions and maintaining mission lifecycle (scheduled, running, completed).
    - `schedule_mission(drone_id, waypoints, start_time, end_time)` returns mission id and persists state.

- templates/ (HTML)
  - `dashboard.html`, `visualization.html`, `history.html`
  - The web UI that receives Socket.IO updates and visualizes drone positions, conflicts and history.

- static/ (JS/CSS)
  - Client-side logic for connecting Socket.IO, handling events, rendering maps, charts, and controls.

---

HTTP API endpoints (summary)

The server exposes these REST endpoints (method, path, purpose):

- GET /                  -> Serve dashboard page
- GET /visualization      -> Serve visualization page
- GET /history/<drone_id> -> Serve history UI for a specific drone

API:
- GET /api/drones
  - Returns current status for all drones.

- GET /api/missions
  - Returns active missions.

- POST /api/schedule
  - Schedule a mission (body: drone_id, waypoints, optional start_time/end_time)
  - Performs deconfliction check before scheduling.

- POST /api/control/<drone_id>
  - Send direct command to a drone (arm, disarm, takeoff, land, rtl, goto, stop).

- POST /api/emergency
  - Emergency stop all drones.

- GET /api/trajectory/<drone_id>
  - Returns recent trajectory points for a drone.

- GET /api/history/conflicts
  - Returns recent and some dummy historical conflict events.

- GET /api/history/statistics
  - Returns aggregated statistics (distance, flight time, per-drone stats) over a time window (default 1 hour).

- GET /api/history/trajectory/<drone_id>
  - Returns detailed trajectory filtered by optional start_time and end_time query parameters.

- GET /api/historical/trajectories
  - Returns historical trajectories for all drones for the past hour.

- GET /api/future/trajectories
  - Returns planned/future trajectories for a given time window (or generated dummy trajectories if DB function missing).

For each endpoint the server returns JSON payloads with `success` flag and data or error messages. Most endpoints emit immediate `drone_update` events over Socket.IO when commands are executed.

---

WebSocket events (summary)

Socket.IO events provided by the server:

Client -> Server:
- `request_historical_playback`:
  - payload: { drone_id, start_time?, end_time? }
  - server replies by emitting `historical_trajectory`.

- `request_update`:
  - payload: none
  - server replies with `drone_update` (latest snapshot).

- `request_historical_state`:
  - payload: { time } (simulation time index)
  - server replies with `historical_update` (currently returns current state).

- `control_drone`:
  - payload: { drone_id, command, ... }
  - server executes command and replies with `control_response` and also emits `drone_update` globally.

Server -> Client:
- `connected`: initial handshake on connect.
- `drone_update`: periodic (default 2 Hz) updates with full drone snapshot and conflict list.
- `conflict_alert`: emitted immediately for a detected conflict.
- `historical_trajectory`: reply to playback requests.
- `historical_update`: reply to historical state requests.
- `control_response`: reply to control commands over socket.

---

Installation & quick start

Requirements:
- Python 3.8+
- Recommended: virtualenv or conda environment

Primary Python dependencies (example):
- Flask
- Flask-SocketIO
- python-socketio
- flask-cors
- eventlet or gevent (optional; the provided server uses threading mode by default)
- (Optional) database client libs: psycopg2, sqlite3, SQLAlchemy

Install dependencies:
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
(If there is no requirements.txt in the repo, create one with the packages above.)

Run server locally:
```bash
# Ensure you set any required environment variables if applicable.
python server.py
# or
python app.py
```

By default the server listens on:
- http://localhost:5000

Notes:
- The provided implementation uses `async_mode='threading'` for Socket.IO and passes `allow_unsafe_werkzeug=True` to `socketio.run()` so it can run with the default Flask dev server for development. For production, use a production-ready WSGI server and recommended Socket.IO async worker (eventlet/gevent) and configure properly.

Docker (optional)
- Optionally provide a Dockerfile that installs dependencies and runs the server. If you want, we can add a sample Dockerfile and docker-compose.yaml.

---

Usage examples

Curl: get system status
```bash
curl -s http://localhost:5000/api/system/status | jq
```

Schedule a mission (example)
```bash
curl -X POST http://localhost:5000/api/schedule \
  -H "Content-Type: application/json" \
  -d '{
    "drone_id": 1,
    "waypoints": [
      {"x": 0, "y": 0, "z": 10},
      {"x": 10, "y": 10, "z": 10}
    ]
  }'
```

Control a drone (takeoff)
```bash
curl -X POST http://localhost:5000/api/control/1 \
  -H "Content-Type: application/json" \
  -d '{"command": "takeoff", "altitude": 15.0 }'
```

WebSocket (Socket.IO client example)
- Use the socket.io-client in JS or Python to connect and listen for `drone_update` and `conflict_alert`.
- Example (browser JS):
```js
const socket = io("http://localhost:5000");
socket.on("connected", (data) => console.log("Connected:", data));
socket.on("drone_update", (update) => {
  // update has { timestamp, drones, conflicts, update_id? }
  console.log(update);
});
socket.emit("request_update"); // ask server for an immediate update
```

---

Configuration & deployment notes

- Update `drone_count` and other defaults in the drone controller initialization if using more or fewer drones.
- Replace dummy modules with production implementations:
  - Implement `database.py` functions to persist & fetch trajectories and missions.
  - Implement `DeconflictionEngine` with your conflict detection & resolution logic.
  - Implement `EnhancedDroneController` that communicates with real drones (MAVLink / DroneKit / vendor SDK).
- Consider using an async worker for Socket.IO in production:
  - eventlet: pip install eventlet and set `async_mode='eventlet'` and run with eventlet server.
- Secure endpoints with authentication (OAuth, API keys, JWT) and serve over TLS (HTTPS).
- Rate limit expensive endpoints if exposing to the public.

---

Development notes & TODOs

Potential improvements:
- Persist trajectories and mission histories to a robust DB (Postgres/Timescale).
- Add ACID-safe mission state transitions and mission logs.
- Add unit tests for deconfliction logic and endpoints.
- Add end-to-end tests that run a simulated drone fleet and verify conflict detection and mission scheduling behavior.
- Implement role-based access control for mission scheduling and emergency commands.
- Add OpenAPI (Swagger) specification and a Postman collection for easy API testing.
- Add Docker & docker-compose manifests for easier deployment.
- Improve time parsing & timezone handling (use pytz / zoneinfo).

---

Security & license

- No authentication is present by default — do NOT expose this server to the public internet without adding proper authentication and TLS.
- License: (Please add a LICENSE file to the repository — e.g., MIT or Apache 2.0.)

---

Contributing

Contributions are welcome. Typical contribution workflow:
1. Fork repository
2. Create feature branch
3. Add tests for new behavior
4. Create a pull request describing changes and rationale

---

Contact / authors

- Repository owner: rushikeshpole (GitHub)
- For implementation-specific questions, open an issue in the repository.

---

If you want, I can:
- Generate a ready-to-run requirements.txt and Dockerfile.
- Add a sample database schema and migration for trajectory storage.
- Produce an OpenAPI specification for all endpoints in this README.
- Create a minimal frontend client to demonstrate typical usage (connect, display drones on a map, playback).
