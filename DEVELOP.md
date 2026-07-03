# Developing Race Condition

Developer guide for the two halves of the project: the **badge app** (MicroPython, ships to Tildagon badges) and the **room server** (aiohttp, runs on Fly.io).

## Prerequisites

| For | You need |
|---|---|
| Running the server + tests | Python 3.12+ (`aiohttp`, `pytest`, `requests`) |
| Flashing badge firmware | Docker (current user in the `docker` group) and a USB cable |
| Deploying the server | `flyctl` and access to the `racecondition` Fly app |

The badge app itself is never run on desktop Python - it imports MicroPython/Tildagon-only modules. It runs on a badge (flashed firmware) or under the test suite (which stubs the hardware).

## Repository layout

See the table in [`README.md`](README.md). In short: `app.py` + `badge/` is the shipped app, `server/` is the room server, `scripts/` is dev tooling, `tests/` holds both suites, and `hexpansion-firmwares/` is a submodule of EEPROM manifests.

## Running the server locally

The server uses flat imports (`from room import Room`), so run it from its own directory:

```bash
cd server
pip install -r requirements.txt
python room_server.py
```

It listens on `0.0.0.0:8000` and serves both the HTTP API and the WebSocket endpoint (`/ws/rooms/{id}`). Web pages: `/` (rooms), `/about`, `/hexpansions`, `/admin`.

Environment variables:

| Var | Default | Purpose |
|---|---|---|
| `RACECONDITION_DB` | `server/racecondition.db` | SQLite path (leaderboard + stats) |
| `ADMIN_PASSWORD` | empty | Password gating the `/api/admin/status` endpoint |

The DB is created on first run. State for live rooms/rounds is held **in memory** - only the leaderboard persists to SQLite.

## Pointing the badge at a local server

The badge's server URL lives in [`badge/room_client.py`](badge/room_client.py):

For local testing, point it at your machine (a hostname/IP the badge can reach on the same network, not `localhost`). `http://` is rewritten to `ws://` and `https://` to `wss://` automatically for the WebSocket connection.

## Tests

Two independent suites - keep them separate, because the badge suite stubs out the hardware modules and the server suite runs against real aiohttp/sqlite.

```bash
python -m pytest tests/badge -q
python -m pytest tests/server -q 
```

- `tests/badge/conftest.py` pre-registers stubs for `machine`, `imu`, `app_components`, `settings`, etc., and shims MicroPython's `time.ticks_ms`/`ticks_diff` so `badge.*` imports on desktop Python.
- `tests/server/conftest.py` just puts `server/` on `sys.path` so the flat imports resolve.

## Hexpansion friendly names

`badge/hexpansion_names.py` is generated from the EEPROM manifests in the `hexpansion-firmwares` submodule. Regenerate after updating the submodule:

```bash
git submodule update --remote hexpansion-firmwares
python3 scripts/generate_hexpansion_names.py
```

Add temporary VID/PID overrides in `MANUAL_FRIENDLY_NAME_OVERRIDES` at the top of the script if a device isn't in the manifests yet.

## Adding hexpansion support
This page is mostly for my own reference for when I (hopefully) look at this again in a couple of years. If you are not me and want to develop support for your own hexpansion, 
see [the site](https://racecondition.area.io/hexpansions)