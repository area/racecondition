#!/usr/bin/env python3
import json
import random
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock


HOST = "0.0.0.0"
PORT = 8000
ROOM_IDS = tuple(range(1, 6))
STALE_BADGE_SECONDS = 20


ADMIN_HTML = """<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>SpaceTeam Admin</title>
    <style>
        :root {
            --bg: #f4efe3;
            --panel: #fff9ec;
            --line: #d7c9aa;
            --text: #2c2314;
            --accent: #0f766e;
            --accent-2: #b45309;
            --ok: #166534;
            --bad: #991b1b;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            color: var(--text);
            font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
            background:
                radial-gradient(circle at 20% 10%, rgba(180, 83, 9, 0.1), transparent 40%),
                radial-gradient(circle at 90% 90%, rgba(15, 118, 110, 0.12), transparent 42%),
                var(--bg);
        }
        header {
            padding: 20px 24px 8px;
        }
        h1 {
            margin: 0;
            font-family: "Space Grotesk", "Avenir Next", sans-serif;
            letter-spacing: 0.02em;
            font-weight: 700;
            font-size: 30px;
        }
        .sub {
            opacity: 0.8;
            margin-top: 6px;
            font-size: 14px;
        }
        main {
            display: grid;
            gap: 16px;
            padding: 16px 24px 28px;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
        }
        .room {
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: 14px;
            padding: 14px;
            box-shadow: 0 8px 18px rgba(44, 35, 20, 0.06);
        }
        .room.room-enter {
            transform: translateY(8px);
            opacity: 0;
            animation: rise 360ms ease forwards;
        }
        .room h2 {
            margin: 0 0 10px;
            font-size: 20px;
            font-family: "Space Grotesk", "Avenir Next", sans-serif;
        }
        .row {
            display: flex;
            justify-content: space-between;
            margin-bottom: 6px;
            font-size: 14px;
        }
        .scores {
            margin: 10px 0;
            padding: 8px;
            border-radius: 10px;
            border: 1px dashed var(--line);
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px;
            font-weight: 600;
        }
        .ok { color: var(--ok); }
        .bad { color: var(--bad); }
        ul {
            margin: 8px 0 0;
            padding-left: 16px;
            font-size: 13px;
        }
        li { margin-bottom: 4px; }
        .muted { opacity: 0.7; }
        .badge {
            display: inline-block;
            border: 1px solid var(--line);
            border-radius: 999px;
            padding: 2px 8px;
            margin-left: 6px;
            font-size: 12px;
            color: var(--accent);
            background: rgba(15, 118, 110, 0.08);
        }
        .footer {
            padding: 4px 24px 20px;
            font-size: 12px;
            opacity: 0.75;
        }
        @keyframes rise {
            to { transform: translateY(0); opacity: 1; }
        }
    </style>
</head>
<body>
    <header>
        <h1>SpaceTeam Admin Console</h1>
        <div class="sub">Live room state, badges, assignments, and scores</div>
    </header>
    <main id="rooms"></main>
    <div class="footer" id="updated">Waiting for data...</div>

    <script>
        const roomsEl = document.getElementById("rooms");
        const updatedEl = document.getElementById("updated");
        const roomNodes = new Map();

        function esc(v) {
            return String(v)
                .replaceAll("&", "&amp;")
                .replaceAll("<", "&lt;")
                .replaceAll(">", "&gt;")
                .replaceAll('"', "&quot;")
                .replaceAll("'", "&#39;");
        }

        function roomCard(room) {
            const badges = room.badges || [];
            const assignments = room.assignments || [];

            const badgeList = badges.length
                ? badges.map((b) => `<li><strong>${esc(b.badge_id)}</strong> <span class="badge">${b.module_count} modules</span></li>`).join("")
                : '<li class="muted">No active badges</li>';

            const assignmentList = assignments.length
                ? assignments.map((a) => `<li><strong>${esc(a.command)}</strong> on ${esc(a.module)} <span class="muted">for ${esc(a.target_badge_id)}</span></li>`).join("")
                : '<li class="muted">No active assignments</li>';

            return `
                <section class="room">
                    <h2>Room ${room.room_id}</h2>
                    <div class="row"><span>Badges</span><strong>${room.badge_count}</strong></div>
                    <div class="row"><span>Assignments</span><strong>${assignments.length}</strong></div>
                    <div class="scores">
                        <div class="ok">Pass: ${room.scores.passed}</div>
                        <div class="bad">Fail: ${room.scores.failed}</div>
                    </div>
                    <div><strong>Badges</strong></div>
                    <ul>${badgeList}</ul>
                    <div><strong>Assignments</strong></div>
                    <ul>${assignmentList}</ul>
                </section>
            `;
        }

        function createRoomNode(room) {
            const wrapper = document.createElement("div");
            wrapper.innerHTML = roomCard(room).trim();
            const section = wrapper.firstElementChild;
            section.classList.add("room-enter");
            section.addEventListener(
                "animationend",
                () => {
                    section.classList.remove("room-enter");
                },
                { once: true }
            );
            return section;
        }

        function updateRooms(rooms) {
            const seen = new Set();

            for (const room of rooms) {
                const key = String(room.room_id);
                seen.add(key);
                const existing = roomNodes.get(key);
                if (!existing) {
                    const node = createRoomNode(room);
                    roomNodes.set(key, node);
                    roomsEl.appendChild(node);
                    continue;
                }

                const wrapper = document.createElement("div");
                wrapper.innerHTML = roomCard(room).trim();
                const next = wrapper.firstElementChild;
                existing.innerHTML = next.innerHTML;
            }

            for (const [key, node] of roomNodes.entries()) {
                if (!seen.has(key)) {
                    node.remove();
                    roomNodes.delete(key);
                }
            }
        }

        async function refresh() {
            try {
                const response = await fetch('/api/admin/status');
                const data = await response.json();
                updateRooms(data.rooms || []);
                updatedEl.textContent = `Updated: ${new Date().toLocaleTimeString()} | Active badges: ${data.total_badges}`;
            } catch (err) {
                updatedEl.textContent = `Admin fetch failed: ${err}`;
            }
        }

        refresh();
        setInterval(refresh, 1000);
    </script>
</body>
</html>
"""


state_lock = Lock()
rooms = {
    room_id: {
        "badges": {},
        "assignments": {},
        "next_assignment_id": 1,
        "scores": {"passed": 0, "failed": 0},
    }
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


def _set_badge(room, badge_id, capabilities):
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
    all_assignments = list(room["assignments"].values())
    if not all_assignments:
        return None

    other_assignments = [
        assignment
        for assignment in all_assignments
        if assignment.get("target_badge_id") != badge_id
    ]

    if other_assignments and random.random() < 0.75:
        return random.choice(other_assignments)
    return random.choice(all_assignments)


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
    room["badges"].clear()
    room["assignments"].clear()
    room["next_assignment_id"] = 1
    room["scores"] = {"passed": 0, "failed": 0}


def _room_admin_snapshot(room_id, room):
    badges = []
    for badge_id, badge in room["badges"].items():
        badges.append(
            {
                "badge_id": badge_id,
                "module_count": len(badge["capabilities"]),
                "last_seen_s": round(_now() - badge["last_seen"], 1),
            }
        )

    assignments = []
    for assignment in room["assignments"].values():
        assignments.append(
            {
                "id": assignment["id"],
                "target_badge_id": assignment["target_badge_id"],
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
                if not room["badges"]:
                    _reset_room(room)
                response = {
                    "room_id": room_id,
                    "status": "left",
                    "badge_count": len(room["badges"]),
                }
            elif action == "poll":
                _apply_result(room, badge_id, payload.get("result"))
                assignment = _assignment_for_badge(room, badge_id)
                display = _display_for_badge(room, badge_id)
                response = {
                    "room_id": room_id,
                    "assignment": assignment,
                    "display": display,
                    "scores": room["scores"],
                    "badge_count": len(room["badges"]),
                }
            else:  # join
                assignment = _assignment_for_badge(room, badge_id)
                display = _display_for_badge(room, badge_id)
                response = {
                    "room_id": room_id,
                    "assignment": assignment,
                    "display": display,
                    "scores": room["scores"],
                    "badge_count": len(room["badges"]),
                }

        self._send_json(200, response)

    def log_message(self, fmt, *args):
        return


def main():
    server = ThreadingHTTPServer((HOST, PORT), RoomRequestHandler)
    print("SpaceTeam room server listening on {}:{}".format(HOST, PORT))
    server.serve_forever()


if __name__ == "__main__":
    main()
