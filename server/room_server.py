#!/usr/bin/env python3
import asyncio
import base64
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

from aiohttp import web, WSMsgType

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ WebSocket
#
# The transport is a real aiohttp server: it owns the HTTP routing, the
# WebSocket handshake, frame (de)masking, ping/pong heartbeat and connection
# timeouts. This module only carries the game's delta-push logic on top.

_WS_PUSH_INTERVAL = 0.1  # seconds between periodic state pushes
_WS_TIMER_JUMP_S = 1.0   # resend the round timer only when it deviates this much
_WS_HEARTBEAT_S = 20.0   # aiohttp pings the badge this often; dead links get dropped


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
REGISTER_HTML_PATH = SCRIPT_DIR / "register.html"
STYLE_CSS_PATH = SCRIPT_DIR / "style.css"


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
    return hashlib.sha256(secret_id.encode()).hexdigest()[:16]


# A room is empty between POST /api/rooms/create and its creator's websocket
# join, and /api/rooms is polled by every index-page viewer — so reaping empty
# rooms on sight would 404 the creator's join. Only reap once past this grace.
_EMPTY_ROOM_GRACE_S = 30


def _new_room():
    with _rooms_lock:
        room_id = next(i for i in range(1, len(rooms) + 2) if i not in rooms)
        rooms[room_id] = Room(room_id, leaderboard=leaderboard, user_registry=user_registry)
    return rooms[room_id]


def _delete_room(room_id):
    with _rooms_lock:
        rooms.pop(room_id, None)


# ------------------------------------------------------------------ HTTP helpers

def _html_response(body, status=200):
    return web.Response(text=body, status=status, content_type="text/html")


def _check_admin_auth(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8")
        _, _, password = decoded.partition(":")
        return password == _ADMIN_PASSWORD
    except Exception:
        return False


def _admin_unauthorized():
    return web.Response(
        status=401, headers={"WWW-Authenticate": 'Basic realm="SpaceTeam Admin"'}
    )


# ------------------------------------------------------------------ HTTP routes

async def index(request):
    return _html_response(_load_html(INDEX_HTML_PATH, "Index page"))


async def admin_page(request):
    if not _check_admin_auth(request):
        return _admin_unauthorized()
    return _html_response(_load_html(ADMIN_HTML_PATH, "Admin page"))


async def about_page(request):
    return _html_response(_load_html(ABOUT_HTML_PATH, "About page"))


async def hexpansions_page(request):
    return _html_response(_load_html(HEXPANSIONS_HTML_PATH, "Hexpansions page"))


async def style_css(request):
    # The shared theme is the one non-HTML asset; it reuses the same
    # mtime-keyed cache as the pages.
    return web.Response(text=_load_html(STYLE_CSS_PATH, "Stylesheet"), content_type="text/css")


async def register_page(request):
    return _html_response(_load_html(REGISTER_HTML_PATH, "Register page"))


async def admin_status(request):
    if not _check_admin_auth(request):
        return _admin_unauthorized()
    with _rooms_lock:
        room_list = list(rooms.values())
    snapshots = [r.admin_snapshot() for r in room_list]
    badge_ids = {b["badge_id"] for s in snapshots for b in s.get("badges", [])}
    return web.json_response({
        "rooms": snapshots,
        "total_badges": sum(s["badge_count"] for s in snapshots),
        "usernames": {bid: user_registry.get(bid) for bid in badge_ids},
    })


async def list_rooms(request):
    with _rooms_lock:
        room_items = list(rooms.items())
    now = time.monotonic()
    result = []
    empty_ids = []
    for rid, r in room_items:
        snap = r.admin_snapshot()
        if snap["badge_count"] == 0:
            if now - r.created_at > _EMPTY_ROOM_GRACE_S:
                empty_ids.append(rid)
        else:
            result.append({
                "room_id": snap["room_id"],
                "badge_count": snap["badge_count"],
                "room_state": snap["room_state"],
            })
    for rid in empty_ids:
        _delete_room(rid)
    return web.json_response({"rooms": result})


async def api_leaderboard(request):
    entries = leaderboard.entries()
    badge_ids = {bid for e in entries for bid in e.get("badges", {}).keys()}
    usernames = {bid: user_registry.get(bid) for bid in badge_ids}
    return web.json_response({"leaderboard": entries, "usernames": usernames})


async def api_stats(request):
    return web.json_response(leaderboard.stats())


async def register(request):
    try:
        payload = await request.json()
    except Exception as exc:
        return web.json_response({"error": "Invalid JSON: {}".format(exc)}, status=400)
    secret_id = payload.get("secret_id")
    username = payload.get("username")
    if not isinstance(secret_id, str) or not secret_id:
        return web.json_response({"error": "secret_id is required"}, status=400)
    if not isinstance(username, str):
        return web.json_response({"error": "username must be a string"}, status=400)
    badge_id = _public_id_from_secret(secret_id)
    if username.strip():
        user_registry.set(badge_id, username)
    else:
        user_registry.delete(badge_id)
    return web.json_response({"ok": True, "username": user_registry.get(badge_id)})


async def create_room(request):
    room = _new_room()
    return web.json_response({"room_id": room.room_id})


async def hurry(request):
    # In-game actions (join/poll/start/dismiss/leave) all happen over the
    # websocket (see ws_handler). The only POST against a room is the admin
    # "hurry" control.
    room_id = int(request.match_info["room_id"])
    with _rooms_lock:
        room = rooms.get(room_id)
    if room is None:
        return web.json_response({"error": "Unknown room"}, status=404)
    if not _check_admin_auth(request):
        return _admin_unauthorized()
    return web.json_response(room.set_timer(5))


# ------------------------------------------------------------------ WebSocket route

async def ws_handler(request):
    room_id = int(request.match_info["room_id"])
    with _rooms_lock:
        room = rooms.get(room_id)
    if room is None:
        return web.Response(status=404)

    ws = web.WebSocketResponse(heartbeat=_WS_HEARTBEAT_S)
    await ws.prepare(request)
    log.info("ws room=%s connected", room_id)

    # Identity is proven by the secret_id the badge sends in its messages (over
    # wss, and in the body rather than the URL so it never lands in access
    # logs). The server derives the public badge_id from it; the badge_id is
    # never accepted as an input, so leaking it (e.g. on the leaderboard) grants
    # no authority. badge_id stays None until the first message carries a
    # secret_id — actions before that are rejected.
    badge_id = None
    badge_short = "??????"
    pending_caps = None
    last_comparable = None
    timer_anchor = None
    joined = False

    async def send_state(state, full=False):
        nonlocal last_comparable, timer_anchor
        now = time.monotonic()
        trs = state.get("time_remaining_s")
        if full:
            payload = dict(state)
            last_comparable = _ws_comparable(state)
            _, timer_anchor = _ws_timer_anchor(trs, None, now)
        else:
            payload, last_comparable = _ws_state_delta(state, last_comparable)
            # The round timer isn't in the comparable, so add it only on a jump
            # (round start is covered by the full send above; this catches the
            # admin "hurry"). The badge interpolates otherwise.
            send_timer, timer_anchor = _ws_timer_anchor(trs, timer_anchor, now)
            if send_timer:
                payload["time_remaining_s"] = trs
            if not payload:
                return  # nothing changed — stay quiet
        body = json.dumps(payload)
        log.info("ws room=%s badge=%s SEND %s", room_id, badge_short, body)
        await ws.send_str(body)

    try:
        while True:
            # Wait up to one push interval for an inbound frame. aiohttp owns the
            # framing, so a partial read can never desync the stream.
            try:
                msg = await ws.receive(timeout=_WS_PUSH_INTERVAL)
            except asyncio.TimeoutError:
                # Idle: once joined, push a delta so the badge sees any change.
                if joined:
                    state = room.poll(badge_id, None, result=None)
                    # A joined badge can be stale-pruned mid-connection (an event
                    # loop stall longer than STALE_BADGE_SECONDS between polls); if
                    # the room has since filled, poll returns a capacity error.
                    # Don't forward it — that would eject a badge from a game it's
                    # actively playing. Stay quiet; a later poll re-adds it once
                    # there's room.
                    if "error" not in state:
                        await send_state(state)
                continue

            if msg.type != WSMsgType.TEXT:
                # CLOSE / CLOSING / CLOSED / ERROR — the connection is done.
                # PING/PONG are handled by aiohttp's heartbeat and never arrive
                # here as TEXT, so anything else means teardown.
                log.info("ws room=%s badge=%s RECV <%s>", room_id, badge_short, msg.type.name)
                break

            m = json.loads(msg.data)
            secret_id = m.get("secret_id")
            if isinstance(secret_id, str) and secret_id:
                badge_id = _public_id_from_secret(secret_id)
                badge_short = badge_id[-6:]
            # Log the message, but never the secret_id — it's the badge's
            # credential. The derived badge_id (above) already identifies the
            # connection in the log.
            redacted = {k: ("<redacted>" if k == "secret_id" else v) for k, v in m.items()}
            log.info("ws room=%s badge=%s RECV %s", room_id, badge_short, json.dumps(redacted))
            if "capabilities" in m:
                pending_caps = m["capabilities"]
            action = m.get("action")

            if action == "leave":
                break
            # Every game action needs an authenticated identity. A message that
            # never carried a secret_id can't act as any badge.
            if badge_id is None:
                state = {"room_id": room_id, "error": "Identify with secret_id first"}
            elif action == "join":
                state = room.join(badge_id, pending_caps)
                joined = "error" not in state
            elif action == "start":
                # Surface start_round's error (e.g. "Badge not in room", "Round
                # already in progress") instead of masking it with a poll snapshot.
                result = room.start_round(badge_id)
                state = result if "error" in result else room.poll(badge_id, pending_caps)
            elif action == "dismiss":
                room.dismiss_score(badge_id)
                state = room.poll(badge_id, pending_caps)
            else:
                state = room.poll(badge_id, pending_caps, result=m.get("result"))
            # Explicit requests always get a full snapshot so the badge fully
            # resyncs on any interaction.
            await send_state(state, full=True)

    except Exception as exc:
        log.info("ws room=%s badge=%s error: %s", room_id, badge_short, exc)
    finally:
        # Only leave if we actually joined: a connection that completes the
        # handshake but never joins (a probe, an early drop) must not call
        # room.leave, which would delete an otherwise-empty room out from under
        # badges about to join it.
        if joined:
            response = room.leave(badge_id)
            if response.get("badge_count", 1) == 0:
                _delete_room(room_id)
            log.info("ws room=%s badge=%s disconnected (left room)", room_id, badge_short)
        else:
            log.info("ws room=%s badge=%s disconnected (never joined)", room_id, badge_short)
    return ws


def build_app():
    app = web.Application()
    app.add_routes([
        web.get("/", index),
        web.get("/style.css", style_css),
        web.get("/admin", admin_page),
        web.get("/about", about_page),
        web.get("/hexpansions", hexpansions_page),
        web.get("/api/admin/status", admin_status),
        web.get("/api/rooms", list_rooms),
        web.get("/api/leaderboard", api_leaderboard),
        web.get("/api/stats", api_stats),
        web.get(r"/register/{secret:[A-Za-z0-9_-]+}", register_page),
        web.get(r"/ws/rooms/{room_id:\d+}", ws_handler),
        web.post("/api/register", register),
        web.post("/api/rooms/create", create_room),
        web.post(r"/api/rooms/{room_id:\d+}/hurry", hurry),
    ])
    return app


def main():
    log.info("Race Condition room server listening on %s:%s", HOST, PORT)
    web.run_app(build_app(), host=HOST, port=PORT, access_log=None, print=None)


if __name__ == "__main__":
    main()
