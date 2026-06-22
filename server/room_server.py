#!/usr/bin/env python3
import base64
import hashlib
import json
import logging
import os
import re
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse as _urlparse, parse_qs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ WebSocket

_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_WS_PUSH_INTERVAL = 0.1  # seconds between periodic state pushes


def _ws_handshake(handler):
    key = handler.headers.get("Sec-WebSocket-Key", "")
    accept = base64.b64encode(
        hashlib.sha1((key + _WS_MAGIC).encode()).digest()
    ).decode()
    # Write the status line directly: aiohttp_ws asserts the response starts
    # with "HTTP/1.1 101 ", but BaseHTTPRequestHandler.send_response() emits
    # HTTP/1.0 by default.
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: {}\r\n"
        "\r\n"
    ).format(accept)
    handler.wfile.write(response.encode())
    handler.wfile.flush()


def _recv_exact(rfile, n):
    # Read exactly n bytes from the connection's buffered reader. Going through
    # rfile (not the raw socket) is essential: BaseHTTPRequestHandler parses the
    # handshake via this same buffered reader, which can read ahead past the
    # request into its buffer; reading the raw socket would strand those bytes
    # and desync the stream.
    chunks = []
    remaining = n
    while remaining:
        chunk = rfile.read(remaining)
        if not chunk:
            raise EOFError("connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _ws_read_frame(rfile, byte1=None):
    # byte1 may be pre-read by the caller (it reads the first byte under a
    # timeout to detect an idle connection, then reads the rest of the frame
    # blocking). Everything after that first byte belongs to one frame.
    if byte1 is None:
        byte1 = _recv_exact(rfile, 1)[0]
    elif isinstance(byte1, (bytes, bytearray)):
        byte1 = byte1[0]
    byte2 = _recv_exact(rfile, 1)[0]
    opcode = byte1 & 0x0F
    masked = bool(byte2 & 0x80)
    length = byte2 & 0x7F
    if length == 126:
        length = struct.unpack("!H", _recv_exact(rfile, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(rfile, 8))[0]
    mask = _recv_exact(rfile, 4) if masked else None
    data = _recv_exact(rfile, length) if length else b""
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return opcode, data


_WS_TIMER_JUMP_S = 1.0  # resend the round timer only when it deviates this much


def _ws_comparable(state):
    """Projection of a poll-state used for delta comparison.

    A poll-state aliases the room's live dicts (scores, badge_scores), which are
    mutated in place as the room updates, so those must be snapshotted or the
    diff would never detect the change. We copy only the dicts we either alias
    or mutate below — not the whole tree — to keep this off the hot path (it runs
    per badge every push interval).

    Every timer is then dropped: the round timer (top-level time_remaining_s)
    and the per-assignment / per-display timers all tick every poll, and the
    badge interpolates all of them locally. So assignment/display are only
    re-sent when the assignment itself changes, and the round timer is handled
    separately by _ws_timer_anchor (sent on a jump, not on the tick).
    """
    c = dict(state)
    c.pop("time_remaining_s", None)
    scores = c.get("scores")
    if isinstance(scores, dict):
        c["scores"] = dict(scores)
    badge_scores = c.get("badge_scores")
    if isinstance(badge_scores, dict):
        c["badge_scores"] = {
            k: dict(v) if isinstance(v, dict) else v for k, v in badge_scores.items()
        }
    for key in ("assignment", "display"):
        sub = c.get(key)
        if isinstance(sub, dict):
            sub = dict(sub)
            sub.pop("time_remaining_s", None)
            c[key] = sub
    return c


def _ws_state_delta(state, last_comparable):
    """Return (payload, comparable).

    payload is the full state when last_comparable is None (the first message),
    otherwise only the keys whose comparable projection changed since the last
    send — possibly empty, in which case there's nothing to push. Note the round
    timer is never included here; see _ws_timer_anchor.
    """
    comparable = _ws_comparable(state)
    if last_comparable is None:
        return dict(state), comparable
    payload = {k: v for k, v in state.items() if comparable.get(k) != last_comparable.get(k)}
    return payload, comparable


def _ws_timer_anchor(trs, anchor, now, threshold=_WS_TIMER_JUMP_S):
    """Decide whether the round timer needs (re)sending.

    Returns (send_it, new_anchor). The timer is sent only when the actual value
    deviates from the linear countdown predicted by the anchor — i.e. on round
    start (no anchor yet) or an admin "hurry" jump — never on the normal tick,
    since the badge counts down locally between updates.
    """
    if trs is None:
        return False, None
    if anchor is None:
        return True, (trs, now)
    last_trs, last_at = anchor
    expected = last_trs - (now - last_at)
    if abs(trs - expected) > threshold:
        return True, (trs, now)
    return False, anchor


def _ws_send(wfile, data, opcode=0x01):
    if isinstance(data, str):
        data = data.encode()
    length = len(data)
    if length < 126:
        header = struct.pack("!BB", 0x80 | opcode, length)
    elif length < 65536:
        header = struct.pack("!BBH", 0x80 | opcode, 126, length)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 127, length)
    wfile.write(header + data)
    wfile.flush()


from room import Room
from leaderboard import SqliteLeaderboard
from usernames import UserRegistry

HOST = "0.0.0.0"
PORT = 8000

_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
if not _ADMIN_PASSWORD:
    import secrets
    _ADMIN_PASSWORD = secrets.token_urlsafe(16)
    print("WARNING: ADMIN_PASSWORD not set. Using generated password: {}".format(_ADMIN_PASSWORD))

SCRIPT_DIR = Path(__file__).resolve().parent
ADMIN_HTML_PATH = SCRIPT_DIR / "admin.html"
INDEX_HTML_PATH = SCRIPT_DIR / "index.html"
ABOUT_HTML_PATH = SCRIPT_DIR / "about.html"
HEXPANSIONS_HTML_PATH = SCRIPT_DIR / "hexpansions.html"
LEADERBOARD_HTML_PATH = SCRIPT_DIR / "leaderboard.html"
REGISTER_HTML_PATH = SCRIPT_DIR / "register.html"


_html_cache: dict = {}

def _load_html(path, label):
    try:
        mtime = path.stat().st_mtime
        cached = _html_cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        content = path.read_text(encoding="utf-8")
        _html_cache[path] = (mtime, content)
        return content
    except OSError as exc:
        return "<h1>{} unavailable</h1><p>{}</p>".format(label, exc)



leaderboard = SqliteLeaderboard()
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
    def _check_admin_auth(self):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            _, _, password = decoded.partition(":")
            return password == _ADMIN_PASSWORD
        except Exception:
            return False

    def _require_admin_auth(self):
        if self._check_admin_auth():
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="SpaceTeam Admin"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

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
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self._handle_ws()
            return

        if self.path == "/":
            self._send_html(200, _load_html(INDEX_HTML_PATH, "Index page"))
            return

        if self.path == "/admin":
            if not self._require_admin_auth():
                return
            self._send_html(200, _load_html(ADMIN_HTML_PATH, "Admin page"))
            return

        if self.path == "/about":
            self._send_html(200, _load_html(ABOUT_HTML_PATH, "About page"))
            return

        if self.path == "/hexpansions":
            self._send_html(200, _load_html(HEXPANSIONS_HTML_PATH, "Hexpansions page"))
            return

        if self.path == "/leaderboard":
            self.send_response(302)
            self.send_header("Location", "/#leaderboard")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if self.path == "/api/admin/status":
            if not self._require_admin_auth():
                return
            with _rooms_lock:
                room_list = list(rooms.values())
            snapshots = [r.admin_snapshot() for r in room_list]
            badge_ids = {b["badge_id"] for s in snapshots for b in s.get("badges", [])}
            self._send_json(200, {
                "rooms": snapshots,
                "total_badges": sum(s["badge_count"] for s in snapshots),
                "usernames": {bid: user_registry.get(bid) for bid in badge_ids},
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
            entries = leaderboard.entries()
            badge_ids = {bid for e in entries for bid in e.get("badges", {}).keys()}
            usernames = {bid: user_registry.get(bid) for bid in badge_ids}
            self._send_json(200, {"leaderboard": entries, "usernames": usernames})
            return

        if self.path == "/api/stats":
            self._send_json(200, leaderboard.stats())
            return

        if re.match(r"^/register/[a-zA-Z0-9_-]+$", self.path):
            self._send_html(200, _load_html(REGISTER_HTML_PATH, "Register page"))
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
            if not isinstance(username, str):
                self._send_json(400, {"error": "username must be a string"})
                return
            badge_id = _public_id_from_secret(secret_id)
            if username.strip():
                user_registry.set(badge_id, username)
            else:
                user_registry.delete(badge_id)
            self._send_json(200, {"ok": True, "username": user_registry.get(badge_id)})
            return

        if self.path == "/api/rooms/create":
            room = _new_room()
            self._send_json(200, {"room_id": room.room_id})
            return

        # In-game actions (join/poll/start/dismiss/leave) all happen over the
        # websocket (see _handle_ws). The only POST against a room is the admin
        # "hurry" control.
        match = re.match(r"^/api/rooms/(\d+)/hurry$", self.path)
        if not match:
            self._send_json(404, {"error": "Not found"})
            return

        room_id = int(match.group(1))
        with _rooms_lock:
            room = rooms.get(room_id)
        if room is None:
            self._send_json(404, {"error": "Unknown room"})
            return

        if not self._require_admin_auth():
            return
        self._send_json(200, room.set_timer(5))

    def _handle_ws(self):
        parsed = _urlparse(self.path)
        match = re.match(r"^/ws/rooms/(\d+)$", parsed.path)
        if not match:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        room_id = int(match.group(1))
        qs = parse_qs(parsed.query)
        badge_id = (qs.get("badge_id") or [None])[0]
        session_token = (qs.get("token") or [None])[0]

        if not badge_id:
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        with _rooms_lock:
            room = rooms.get(room_id)
        if room is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        _ws_handshake(self)
        badge_short = badge_id[-6:] if badge_id else "?"
        log.info("ws room=%s badge=%s connected", room_id, badge_short)

        sock = self.connection
        rfile = self.rfile
        pending_caps = None
        last_comparable = [None]  # boxed so the closure can rebind it
        timer_anchor = [None]

        def send_state(state, full=False):
            now = time.monotonic()
            trs = state.get("time_remaining_s")
            if full:
                payload = dict(state)
                last_comparable[0] = _ws_comparable(state)
                _, timer_anchor[0] = _ws_timer_anchor(trs, None, now)
            else:
                payload, last_comparable[0] = _ws_state_delta(state, last_comparable[0])
                # The round timer isn't in the comparable, so add it only on a
                # jump (round start is covered by the full send above; this
                # catches the admin "hurry"). The badge interpolates otherwise.
                send_timer, timer_anchor[0] = _ws_timer_anchor(trs, timer_anchor[0], now)
                if send_timer:
                    payload["time_remaining_s"] = trs
                if not payload:
                    return  # nothing changed — stay quiet
            body = json.dumps(payload)
            log.info("ws room=%s badge=%s SEND %s", room_id, badge_short, body)
            _ws_send(self.wfile, body)

        joined = False
        try:
            while True:
                # Block up to one push interval for the first byte of a frame.
                # The timeout only ever fires at a frame boundary (we go blocking
                # for the rest of the frame below), so the stream can't desync.
                sock.settimeout(_WS_PUSH_INTERVAL)
                try:
                    first = rfile.read(1)
                except socket.timeout:
                    first = None
                except (OSError, EOFError):
                    break

                if first is None:
                    # No inbound frame within the interval: once joined, push a
                    # delta so the badge sees any change (and nothing if idle).
                    if joined:
                        try:
                            state = room.poll(badge_id, None,
                                              result=None, session_token=session_token)
                            send_state(state)
                        except OSError:
                            break
                    continue
                if not first:
                    break  # EOF / connection closed

                # Got the first byte; read the remainder of this frame blocking.
                sock.settimeout(None)
                try:
                    opcode, data = _ws_read_frame(rfile, first)
                except (OSError, EOFError):
                    break

                if opcode == 8:  # CLOSE
                    log.info("ws room=%s badge=%s RECV <close>", room_id, badge_short)
                    break
                if opcode == 9:  # PING -> PONG
                    log.info("ws room=%s badge=%s RECV <ping>", room_id, badge_short)
                    _ws_send(self.wfile, data, opcode=0x0A)
                    continue
                if opcode == 1 and data:  # TEXT
                    log.info("ws room=%s badge=%s RECV %s", room_id, badge_short, data)
                    msg = json.loads(data)
                    if "capabilities" in msg:
                        pending_caps = msg["capabilities"]
                    if "session_token" in msg:
                        session_token = msg["session_token"]
                    action = msg.get("action")

                    if action == "leave":
                        break
                    if action == "join":
                        state = room.join(badge_id, pending_caps)
                        joined = "error" not in state
                        if state.get("session_token"):
                            session_token = state["session_token"]
                    elif action == "start":
                        # Surface start_round's error (e.g. "Badge not in room",
                        # "Round already in progress") instead of masking it with
                        # a normal poll snapshot.
                        result = room.start_round(badge_id)
                        state = result if "error" in result else room.poll(
                            badge_id, pending_caps, session_token=session_token)
                    elif action == "dismiss":
                        room.dismiss_score(badge_id)
                        state = room.poll(badge_id, pending_caps, session_token=session_token)
                    else:
                        state = room.poll(badge_id, pending_caps,
                                          result=msg.get("result"), session_token=session_token)
                    # Explicit requests always get a full snapshot so the
                    # badge fully resyncs on any interaction.
                    send_state(state, full=True)

        except Exception as exc:
            log.info("ws room=%s badge=%s error: %s", room_id, badge_short, exc)
        finally:
            # Only leave if we actually joined: a connection that completes the
            # handshake but never joins (a probe, an early drop) must not call
            # room.leave, which would delete an otherwise-empty room out from
            # under badges about to join it.
            if joined:
                response = room.leave(badge_id)
                if response.get("badge_count", 1) == 0:
                    _delete_room(room_id)
                log.info("ws room=%s badge=%s disconnected (left room)", room_id, badge_short)
            else:
                log.info("ws room=%s badge=%s disconnected (never joined)", room_id, badge_short)

    def log_message(self, format, *args):
        return


def main():
    server = ThreadingHTTPServer((HOST, PORT), RoomRequestHandler)
    print("Race Condition room server listening on {}:{}".format(HOST, PORT))
    server.serve_forever()


if __name__ == "__main__":
    main()
