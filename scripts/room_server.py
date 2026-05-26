#!/usr/bin/env python3
import json
import random
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock


HOST = "0.0.0.0"
PORT = 8000
ROOM_IDS = tuple(range(1, 6))
STALE_BADGE_SECONDS = 20
COLOURS = ["red", "green", "blue"]

SCRIPT_DIR = Path(__file__).resolve().parent
ADMIN_HTML_PATH = SCRIPT_DIR / "admin.html"


def _load_admin_html():
    try:
        return ADMIN_HTML_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        return "<h1>Admin page unavailable</h1><p>{}</p>".format(exc)


ADMIN_HTML = _load_admin_html()


state_lock = Lock()


def _new_room_state():
    return {
        "badges": {},
        "assignments": {},
        "badge_colours": {},
        "next_assignment_id": 1,
        "scores": {"passed": 0, "failed": 0},
    }


rooms = {
    room_id: _new_room_state()
    for room_id in ROOM_IDS
}


def _now():
    return time.monotonic()


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
        cleaned_commands = []
        for command in commands:
            if isinstance(command, str) and command:
                cleaned_commands.append(command)
        if cleaned_commands:
            normalized[module] = tuple(cleaned_commands)
    return normalized


def _prune_stale_badges(room):
    cutoff = _now() - STALE_BADGE_SECONDS
    stale_badges = []
    for badge_id, info in room["badges"].items():
        if info["last_seen"] < cutoff:
            stale_badges.append(badge_id)

    for badge_id in stale_badges:
        room["badges"].pop(badge_id, None)
        room["assignments"].pop(badge_id, None)
        room["badge_colours"].pop(badge_id, None)


def _set_badge(room, badge_id, capabilities):
    if badge_id not in room["badge_colours"]:
        idx = len(room["badge_colours"]) % len(COLOURS)
        room["badge_colours"][badge_id] = COLOURS[idx]
    room["badges"][badge_id] = {
        "capabilities": capabilities,
        "last_seen": _now(),
    }


def _badge_can_run(room, badge_id, module, command):
    badge = room["badges"].get(badge_id)
    if not badge:
        return False
    commands = badge["capabilities"].get(module, ())
    return command in commands


def _global_command_pool(room):
    pool = []
    for badge in room["badges"].values():
        for module, commands in badge["capabilities"].items():
            for command in commands:
                pool.append((module, command))
    return pool


def _assignment_for_badge(room, badge_id):
    existing = room["assignments"].get(badge_id)
    if existing:
        return existing

    if badge_id not in room["badges"]:
        return None

    pool = _global_command_pool(room)
    if not pool:
        return None

    candidates = []
    for module, command in pool:
        if _badge_can_run(room, badge_id, module, command):
            candidates.append((module, command))

    if not candidates:
        return None

    module, command = random.choice(candidates)
    assignment_id = "{}-{}".format(id(room), room["next_assignment_id"])
    room["next_assignment_id"] += 1
    assignment = {
        "id": assignment_id,
        "target_badge_id": badge_id,
        "module": module,
        "command": command,
        "issued_at": _now(),
    }
    room["assignments"][badge_id] = assignment
    return assignment


def _display_for_badge(room, badge_id):
    all_assignments = list(room["assignments"].items())
    if not all_assignments:
        return None

    other_assignments = [
        (tid, assignment)
        for tid, assignment in all_assignments
        if tid != badge_id
    ]

    if other_assignments and random.random() < 0.75:
        target_id, assignment = random.choice(other_assignments)
    else:
        target_id, assignment = random.choice(all_assignments)

    return {
        "module": assignment["module"],
        "command": assignment["command"],
        "target_colour": room["badge_colours"].get(target_id),
    }


def _apply_result(room, badge_id, result):
    if not isinstance(result, dict):
        return

    expected = room["assignments"].get(badge_id)
    if not expected:
        return

    if result.get("assignment_id") != expected.get("id"):
        return

    status = result.get("status")
    if status == "passed":
        room["scores"]["passed"] += 1
        room["assignments"].pop(badge_id, None)
    elif status == "failed":
        room["scores"]["failed"] += 1
        room["assignments"].pop(badge_id, None)


def _reset_room(room):
    """Reset room state when empty."""
    new_state = _new_room_state()
    room.clear()
    room.update(new_state)


def _room_poll_response(room_id, room, badge_id):
    assignment = _assignment_for_badge(room, badge_id)
    display = _display_for_badge(room, badge_id)
    return {
        "room_id": room_id,
        "assignment": assignment,
        "display": display,
        "scores": room["scores"],
        "badge_count": len(room["badges"]),
        "colour": room["badge_colours"].get(badge_id),
    }


def _room_admin_snapshot(room_id, room):
    badges = []
    for badge_id, badge in room["badges"].items():
        badges.append(
            {
                "badge_id": badge_id,
                "colour": room["badge_colours"].get(badge_id),
                "module_count": len(badge["capabilities"]),
                "last_seen_s": round(_now() - badge["last_seen"], 1),
            }
        )

    assignments = []
    for target_id, assignment in room["assignments"].items():
        assignments.append(
            {
                "id": assignment["id"],
                "target_badge_id": target_id,
                "target_colour": room["badge_colours"].get(target_id),
                "module": assignment["module"],
                "command": assignment["command"],
                "age_s": round(_now() - assignment["issued_at"], 1),
            }
        )

    return {
        "room_id": room_id,
        "badge_count": len(room["badges"]),
        "scores": room["scores"],
        "badges": badges,
        "assignments": assignments,
    }


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
            with state_lock:
                snapshot_rooms = []
                total_badges = 0
                for room_id in ROOM_IDS:
                    room = rooms[room_id]
                    _prune_stale_badges(room)
                    room_data = _room_admin_snapshot(room_id, room)
                    snapshot_rooms.append(room_data)
                    total_badges += room_data["badge_count"]
            self._send_json(
                200,
                {
                    "rooms": snapshot_rooms,
                    "total_badges": total_badges,
                },
            )
            return

        self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        match = re.match(r"^/api/rooms/(\d+)/(join|poll|leave)$", self.path)
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

        capabilities = _normalize_capabilities(payload.get("capabilities"))

        with state_lock:
            room = rooms[room_id]
            _prune_stale_badges(room)
            _set_badge(room, badge_id, capabilities)

            if action == "leave":
                room["badges"].pop(badge_id, None)
                room["assignments"].pop(badge_id, None)
                room["badge_colours"].pop(badge_id, None)
                if not room["badges"]:
                    _reset_room(room)
                response = {
                    "room_id": room_id,
                    "status": "left",
                    "badge_count": len(room["badges"]),
                }
            elif action == "poll":
                _apply_result(room, badge_id, payload.get("result"))
                response = _room_poll_response(room_id, room, badge_id)
            else:  # join
                response = _room_poll_response(room_id, room, badge_id)

        self._send_json(200, response)

    def log_message(self, format, *args):
        return


def main():
    server = ThreadingHTTPServer((HOST, PORT), RoomRequestHandler)
    print("SpaceTeam room server listening on {}:{}".format(HOST, PORT))
    server.serve_forever()


if __name__ == "__main__":
    main()
