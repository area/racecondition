## VID/PID friendly-name lookup

Generate the lookup module used by the app from EEPROM manifests in
the `hexpansion-firmwares` submodule:

```bash
python3 scripts/generate_hexpansion_names.py
```

This writes `app/hexpansion_names.py`, which is imported by the app to
display a friendly device name when a hexpansion is detected.

## Server-Controlled Rooms

The app now runs in server-controlled mode:

- A badge joins one of 5 rooms.
- The badge reports its available module commands to the server.
- The server issues one assignment for that badge.
- The command shown on screen may be another badge's assignment from the same room.

### Run The Room Server

From the repo root:

```bash
python3 scripts/room_server.py
```

By default this listens on `0.0.0.0:8000`.

### Admin Webpage

Once the server is running, open:

- `http://<server-host>:8000/admin`

This live dashboard shows room occupancy, active assignments, and pass/fail scores.

Raw status JSON is available at:

- `http://<server-host>:8000/api/admin/status`

The badge client URL is set in `app/room_client.py` as `DEFAULT_SERVER_URL`.
Change this to the server's reachable IP/hostname for your network.
