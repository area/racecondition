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


def _load_html(path, label):
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        return "<h1>{} unavailable</h1><p>{}</p>".format(label, exc)


ADMIN_HTML = _load_html(ADMIN_HTML_PATH, "Admin page")
LEADERBOARD_HTML = _load_html(LEADERBOARD_HTML_PATH, "Leaderboard page")

REGISTER_HTML = """<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Badge Name</title>
<style>
body{font-family:sans-serif;max-width:400px;margin:40px auto;padding:0 20px;text-align:center;background:#111;color:#eee}
h2{color:#4f4}
input{width:100%;padding:12px;font-size:20px;box-sizing:border-box;margin:10px 0;border:2px solid #555;border-radius:8px;background:#222;color:#eee}
button{width:100%;padding:14px;font-size:18px;background:#2a2;color:#fff;border:none;border-radius:8px;cursor:pointer}
#msg{margin-top:16px;font-size:18px;color:#4f4}
</style>
</head>
<body>
<h2>Set your badge name</h2>
<input type="text" id="name" maxlength="16" placeholder="Your name" autocomplete="off" autocorrect="off" spellcheck="false">
<button onclick="save()">Save</button>
<div id="msg"></div>
<script>
var badge_id=location.pathname.split('/').pop();
function save(){
  var name=document.getElementById('name').value.trim();
  if(!name){document.getElementById('msg').textContent='Enter a name first';return;}
  fetch('/api/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({badge_id:badge_id,username:name})})
  .then(function(r){return r.json();})
  .then(function(d){
    if(d.ok){document.getElementById('msg').textContent='\\u2713 Name set to: '+d.username;}
    else{document.getElementById('msg').textContent='Error: '+(d.error||'unknown');}
  });
}
document.getElementById('name').addEventListener('keydown',function(e){if(e.key==='Enter')save();});
</script>
</body>
</html>"""

leaderboard = FilesystemLeaderboard()
user_registry = UserRegistry()
rooms = {}
_rooms_lock = threading.Lock()


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
            self._send_json(200, {"leaderboard": leaderboard.entries()})
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
            badge_id = payload.get("badge_id")
            username = payload.get("username")
            if not isinstance(badge_id, str) or not badge_id:
                self._send_json(400, {"error": "badge_id is required"})
                return
            if not isinstance(username, str) or not username.strip():
                self._send_json(400, {"error": "username is required"})
                return
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
