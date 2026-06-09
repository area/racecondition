#!/usr/bin/env python3
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from room import Room
from leaderboard import FilesystemLeaderboard
from usernames import UserRegistry

HOST = "0.0.0.0"
PORT = 8000

SCRIPT_DIR = Path(__file__).resolve().parent
ADMIN_HTML_PATH = SCRIPT_DIR / "admin.html"
LEADERBOARD_HTML_PATH = SCRIPT_DIR / "leaderboard.html"
REGISTER_HTML_PATH = SCRIPT_DIR / "register.html"


def _load_html(path, label):
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        return "<h1>{} unavailable</h1><p>{}</p>".format(label, exc)


ADMIN_HTML = _load_html(ADMIN_HTML_PATH, "Admin page")
LEADERBOARD_HTML = _load_html(LEADERBOARD_HTML_PATH, "Leaderboard page")
REGISTER_HTML = _load_html(REGISTER_HTML_PATH, "Register page")

leaderboard = FilesystemLeaderboard()
user_registry = UserRegistry()
rooms = {}
_rooms_lock = threading.Lock()


def _public_id_from_secret(secret_id):
    import hashlib
    return hashlib.sha256(secret_id.encode()).hexdigest()[:16]


def _new_room():
    with _rooms_lock:
        room_id = next(i for i in range(1, len(rooms) + 2) if i not in rooms)
        rooms[room_id] = Room(room_id, leaderboard=leaderboard, user_registry=user_registry)
    return rooms[room_id]


def _delete_room(room_id):
    with _rooms_lock:
        rooms.pop(room_id, None)


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

        if self.path == "/leaderboard":
            self._send_html(200, LEADERBOARD_HTML)
            return

        if self.path == "/api/admin/status":
            with _rooms_lock:
                room_list = list(rooms.values())
            snapshots = [r.admin_snapshot() for r in room_list]
            self._send_json(200, {
                "rooms": snapshots,
                "total_badges": sum(s["badge_count"] for s in snapshots),
            })
            return

        if self.path == "/api/rooms":
            with _rooms_lock:
                room_items = list(rooms.items())
            result = []
            empty_ids = []
            for rid, r in room_items:
                snap = r.admin_snapshot()
                if snap["badge_count"] == 0:
                    empty_ids.append(rid)
                else:
                    result.append({
                        "room_id": snap["room_id"],
                        "badge_count": snap["badge_count"],
                        "room_state": snap["room_state"],
                    })
            for rid in empty_ids:
                _delete_room(rid)
            self._send_json(200, {"rooms": result})
            return

        if self.path == "/api/leaderboard":
            self._send_json(200, {"leaderboard": leaderboard.entries(), "usernames": user_registry.all()})
            return

        if re.match(r"^/register/[a-zA-Z0-9_-]+$", self.path):
            self._send_html(200, REGISTER_HTML)
            return

        self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        if self.path == "/api/register":
            try:
                payload = self._json_body()
            except Exception as exc:
                self._send_json(400, {"error": "Invalid JSON: {}".format(exc)})
                return
            secret_id = payload.get("secret_id")
            username = payload.get("username")
            if not isinstance(secret_id, str) or not secret_id:
                self._send_json(400, {"error": "secret_id is required"})
                return
            if not isinstance(username, str) or not username.strip():
                self._send_json(400, {"error": "username is required"})
                return
            badge_id = _public_id_from_secret(secret_id)
            user_registry.set(badge_id, username)
            self._send_json(200, {"ok": True, "username": user_registry.get(badge_id)})
            return

        if self.path == "/api/rooms/create":
            room = _new_room()
            self._send_json(200, {"room_id": room.room_id})
            return

        match = re.match(r"^/api/rooms/(\d+)/(join|poll|leave|start|dismiss|hurry)$", self.path)
        if not match:
            self._send_json(404, {"error": "Not found"})
            return

        room_id = int(match.group(1))
        action = match.group(2)

        with _rooms_lock:
            room = rooms.get(room_id)
        if room is None:
            self._send_json(404, {"error": "Unknown room"})
            return

        try:
            payload = self._json_body()
        except Exception as exc:
            self._send_json(400, {"error": "Invalid JSON: {}".format(exc)})
            return

        if action == "hurry":
            self._send_json(200, room.set_timer(5))
            return

        badge_id = payload.get("badge_id")
        if not isinstance(badge_id, str) or not badge_id:
            self._send_json(400, {"error": "badge_id is required"})
            return

        if action == "join":
            response = room.join(badge_id, payload.get("capabilities"))
            if "error" in response:
                self._send_json(400, response)
                return
        elif action == "poll":
            response = room.poll(badge_id, payload.get("capabilities"),
                                 result=payload.get("result"),
                                 session_token=payload.get("session_token"))
        elif action == "leave":
            response = room.leave(badge_id)
            if response.get("badge_count", 1) == 0:
                _delete_room(room_id)
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
