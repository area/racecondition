## Setup

### Badge client URL

Set the server address in `badge/room_client.py`:

```python
DEFAULT_SERVER_URL = "http://<server-ip>:8000"
```

### Hexpansion name lookup

Generate the friendly-name module from EEPROM manifests in the `hexpansion-firmwares` submodule:

```bash
python3 scripts/generate_hexpansion_names.py
```

This writes `badge/hexpansion_names.py`, imported by the app to display a friendly device name when a hexpansion is detected.

## Badge app

### Layout

The entry point is `app.py` at the **repo root** — a thin shim that re-exports the app class (`from .badge.app import RaceConditionApp; __app_export__ = RaceConditionApp`). The actual app code lives in the `badge/` subpackage: `badge/app.py` and its sibling modules (`session.py`, `render.py`, `leds.py`, …) plus the `hexpansion/` and `lib/` subpackages. Everything else (`server/`, `tests/`, `scripts/`, `docs/`, …) is dev/tooling and is excluded from the published tarball via `export-ignore` in `.gitattributes`.

The installer requires `app.py` at the tarball root, so it can't *be* a folder — a file `app.py` and a directory `app/` would collide on the same module name, which is why the code folder is named `badge/` rather than `app/`.

When published, the app store runs `git archive` of the release tag and unpacks it into `apps/<owner>_<title>/` on the badge, then imports `apps.<name>.app` (the shim) and reads `__app_export__`, which pulls in `apps.<name>.badge.*`. No `metadata.json` is needed — the launcher's defaults (`apps.<name>.app` + `__app_export__`) match this layout, and the store regenerates its own manifest on install. `tests/test_publish_archive.py` guards that the archive stays app-only (`app.py`, `badge/`, `tildagon.toml`).

### Running it locally

**Simulator** ([`badge-2024-software/sim`](https://github.com/emfcamp/badge-2024-software)) — it maps `/apps` to `sim/apps/` and imports `apps.<folder>.app`. Symlink this repo in so edits live-reload:

```bash
ln -s "$(pwd)" /path/to/badge-2024-software/sim/apps/racecondition
cd /path/to/badge-2024-software/sim && pipenv run python run.py
```

Under CPython the sim wants the app folder to be a package; if `apps.racecondition` (or the nested `badge`) isn't picked up, add an empty `__init__.py` to the app folder and to `badge/` **in the sim copy only** (not in this repo — MicroPython on the badge doesn't need them).

**Real badge** (highest fidelity — tests exactly what ships) — build the published payload with `git archive` and copy it over with [`mpremote`](https://docs.micropython.org/en/latest/reference/mpremote.html):

```bash
git archive --worktree-attributes --format=tar "$(git write-tree)" | tar -x -C /tmp/rc-payload
mpremote connect <port> fs cp -r /tmp/rc-payload/* :/apps/racecondition/
```

Reboot/relaunch; the launcher finds the app with no `metadata.json`.

> When **sideloading** (sim or `mpremote`, not via the store), the app is named after its folder. To show "Race Condition" / "Games" while developing, drop a minimal `{ "name": "Race Condition", "category": "Games" }` into the *deployed* copy's `metadata.json` — don't commit it; the store takes name/category from `tildagon.toml`'s `[app]` block.

### Unit tests

```bash
python -m pytest
```

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
| `RACECONDITION_DB` | `server/racecondition.db` | SQLite file path; set to the volume path in production |

If `ADMIN_PASSWORD` is not set, a random password is generated and printed to stdout on each start.

### Database

SQLite. The path defaults to `server/racecondition.db` and is overridable via the `RACECONDITION_DB` environment variable (set to the mounted volume path in production). Created automatically on first run. Contains `usernames` and `leaderboard_entries` tables.

## Deployment

The server runs on [Fly.io](https://fly.io) as a **single always-on machine**. Because room state is held in memory and persisted to one SQLite file, it must not horizontally scale or auto-stop — `fly.toml` pins it to one machine.

### One-time setup

```bash
fly launch --no-deploy                                  # uses the committed fly.toml
fly volumes create racecondition_data --region lhr --size 1 # persistent SQLite volume
fly secrets set ADMIN_PASSWORD=<password>
fly certs add racecondition.area.io                     # then add the shown records in area.io DNS
```

### Continuous deployment

`.github/workflows/ci.yml` runs both test suites (server and badge-app) on every pull request and push to `main`. On a push to `main`, if both suites are green, it deploys with `flyctl` — pull requests run the tests but never deploy. Add a `FLY_API_TOKEN` repository secret (create one with `fly tokens create deploy`).

## Web pages

| Path | Auth | Purpose |
|---|---|---|
| `/` | — | Public dashboard (leaderboard, room list) |
| `/admin` | Basic auth | Live room monitor: occupancy, assignments, scores |
| `/hexpansions` | — | Connected hexpansion types across all rooms |
| `/register/<token>` | — | Badge username registration page |

## API

In-game actions (join, poll, submit result, start, dismiss, leave) all flow over
the WebSocket — see below. The HTTP endpoints cover only room discovery/creation,
username registration, and admin controls.

### Public

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/rooms` | Active rooms with badge count and state |
| GET | `/api/leaderboard` | All completed round scores |
| GET | `/api/stats` | Aggregate leaderboard stats |
| POST | `/api/rooms/create` | Create a new room |
| POST | `/api/register` | Register or clear a badge username |
| WS | `/ws/rooms/<id>` | In-game session: join, poll, result, start, dismiss, leave |

### Admin (Basic auth required)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/admin/status` | Full snapshot of all rooms |
| POST | `/api/rooms/<id>/hurry` | Set round timer to 5 seconds |
