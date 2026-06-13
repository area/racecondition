## Setup

### Badge client URL

Set the server address in `app/room_client.py`:

```python
DEFAULT_SERVER_URL = "http://<server-ip>:8000"
```

### Hexpansion name lookup

Generate the friendly-name module from EEPROM manifests in the `hexpansion-firmwares` submodule:

```bash
python3 scripts/generate_hexpansion_names.py
```

This writes `app/hexpansion_names.py`, imported by the app to display a friendly device name when a hexpansion is detected.

## Running the server

From the `server/` directory:

```bash
cd server
python3 room_server.py
```

Listens on `0.0.0.0:8000` by default.

### Environment variables

| Variable | Default | Notes |
|---|---|---|
| `ADMIN_PASSWORD` | (random, printed on startup) | Password for admin-protected endpoints |

If `ADMIN_PASSWORD` is not set, a random password is generated and printed to stdout on each start.

### Database

SQLite at `server/tildateam.db`. Created automatically on first run. Contains `usernames` and `leaderboard_entries` tables.

## Web pages

| Path | Auth | Purpose |
|---|---|---|
| `/` | — | Public dashboard (leaderboard, room list) |
| `/admin` | Basic auth | Live room monitor: occupancy, assignments, scores |
| `/hexpansions` | — | Connected hexpansion types across all rooms |
| `/register/<token>` | — | Badge username registration page |

## API

### Public

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/rooms` | Active rooms with badge count and state |
| GET | `/api/leaderboard` | All completed round scores |
| POST | `/api/rooms/create` | Create a new room |
| POST | `/api/register` | Register or clear a badge username |
| POST | `/api/rooms/<id>/join` | Join a room |
| POST | `/api/rooms/<id>/poll` | Poll for state, submit result |
| POST | `/api/rooms/<id>/leave` | Leave a room |
| POST | `/api/rooms/<id>/start` | Mark ready / start round |
| POST | `/api/rooms/<id>/dismiss` | Dismiss the score screen |

### Admin (Basic auth required)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/admin/status` | Full snapshot of all rooms |
| POST | `/api/rooms/<id>/hurry` | Set round timer to 5 seconds |
