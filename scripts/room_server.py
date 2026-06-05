#!/usr/bin/env python3
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from room import Room

HOST = "0.0.0.0"
PORT = 8000
ROOM_IDS = tuple(range(1, 6))

SCRIPT_DIR = Path(__file__).resolve().parent
ADMIN_HTML_PATH = SCRIPT_DIR / "admin.html"


def _load_admin_html():
    try:
        return ADMIN_HTML_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        return "<h1>Admin page unavailable</h1><p>{}</p>".format(exc)


ADMIN_HTML = _load_admin_html()

rooms = {room_id: Room(room_id) for room_id in ROOM_IDS}


def _normalize_capabilities(capabilities):
    normalized = {}
    if not isinstance(capabilities, list):
        return normalized
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        module = item.get("module")
        commands = item.get("commands")
        if not isinstance(module, str) or not isinstance(commands, list):
            continue
        cleaned = [c for c in commands if isinstance(c, str) and c]
        if cleaned:
            normalized[module] = tuple(cleaned)
    return normalized


class RoomRequestHandler(BaseHTTPRequestHandler):
    def _json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _send_json(self, status, payload):
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_html(self, status, body):
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path in ("/", "/admin"):
            self._send_html(200, ADMIN_HTML)
            return

        if self.path == "/api/admin/status":
            snapshots = [rooms[room_id].admin_snapshot() for room_id in ROOM_IDS]
            self._send_json(200, {
                "rooms": snapshots,
                "total_badges": sum(s["badge_count"] for s in snapshots),
            })
            return

        self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        match = re.match(r"^/api/rooms/(\d+)/(join|poll|leave|start|dismiss)$", self.path)
        if not match:
            self._send_json(404, {"error": "Not found"})
            return

        room_id = int(match.group(1))
        action = match.group(2)
        if room_id not in ROOM_IDS:
            self._send_json(404, {"error": "Unknown room"})
            return

        try:
            payload = self._json_body()
        except Exception as exc:
            self._send_json(400, {"error": "Invalid JSON: {}".format(exc)})
            return

        badge_id = payload.get("badge_id")
        if not isinstance(badge_id, str) or not badge_id:
            self._send_json(400, {"error": "badge_id is required"})
            return

        room = rooms[room_id]

        if action == "join":
            response = room.join(badge_id, _normalize_capabilities(payload.get("capabilities")))
        elif action == "poll":
            response = room.poll(badge_id, _normalize_capabilities(payload.get("capabilities")),
                                 result=payload.get("result"))
        elif action == "leave":
            response = room.leave(badge_id)
        elif action == "start":
            response = room.start_round(badge_id)
        else:  # dismiss
            response = room.dismiss_score(badge_id)

        self._send_json(200, response)

    def log_message(self, format, *args):
        return


def main():
    server = ThreadingHTTPServer((HOST, PORT), RoomRequestHandler)
    print("Tildateam room server listening on {}:{}".format(HOST, PORT))
    server.serve_forever()


if __name__ == "__main__":
    main()
