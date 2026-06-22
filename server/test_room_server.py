#!/usr/bin/env python3
"""
Integration tests for room_server.py.

Runs the server in a background thread. In-game actions (join/poll/start/
result/dismiss/leave) go over the websocket via the WSClient helper below;
room discovery/creation, stats and admin use plain HTTP.

The periodic state push is disabled (_WS_PUSH_INTERVAL set very high) so that
every websocket send has exactly one matching response — request/response
testing without push frames racing in.
"""
import base64
import json
import os
import socket
import struct
import time
import unittest
import urllib.request
import urllib.error
from threading import Thread
from http.server import ThreadingHTTPServer
from pathlib import Path

import importlib.util

_spec = importlib.util.spec_from_file_location("room_server", Path(__file__).parent / "room_server.py")
room_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(room_server)

_rc_spec = importlib.util.spec_from_file_location(
    "room_client", Path(__file__).parents[1] / "app" / "room_client.py"
)
_rc_module = importlib.util.module_from_spec(_rc_spec)
_rc_spec.loader.exec_module(_rc_module)
RoomClient = _rc_module.RoomClient

_room_spec = importlib.util.spec_from_file_location("room", Path(__file__).parent / "room.py")
_room_module = importlib.util.module_from_spec(_room_spec)
_room_spec.loader.exec_module(_room_module)
Room = _room_module.Room
MAX_BADGES = _room_module.MAX_BADGES
COLOURS = _room_module.COLOURS

from leaderboard import SqliteLeaderboard


def _make_room(room_id):
    return Room(room_id, leaderboard=SqliteLeaderboard(":memory:"))

TEST_HOST = "127.0.0.1"
TEST_PORT = 18000
BASE_URL = "http://{}:{}".format(TEST_HOST, TEST_PORT)

GPS_CAPS = [{"module": "GPS", "commands": ["move 5m away"]}]
MEGADRIVE_CAPS = [{"module": "MegaDrive", "commands": ["a", "b"]}]


class WSHandshakeError(Exception):
    def __init__(self, status):
        super().__init__(status)
        self.status = status


def _ws_status_for(path):
    """Open a websocket handshake for an arbitrary path; return the status line."""
    s = socket.create_connection((TEST_HOST, TEST_PORT))
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((
            "GET {} HTTP/1.1\r\nHost: {}\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: {}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).format(path, TEST_HOST, key).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(1024)
            if not chunk:
                break
            buf += chunk
        return buf.split(b"\r\n", 1)[0].decode()
    finally:
        s.close()


class WSClient:
    """Minimal websocket test client — one connection represents one badge."""

    def __init__(self, room_id, badge_id):
        self.sock = socket.create_connection((TEST_HOST, TEST_PORT))
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            "GET /ws/rooms/{}?badge_id={} HTTP/1.1\r\n"
            "Host: {}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Key: {}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).format(room_id, badge_id, TEST_HOST, key).encode())
        status = self._read_status_line()
        if status != "HTTP/1.1 101 Switching Protocols":
            self.sock.close()
            raise WSHandshakeError(status)

    def _read_status_line(self):
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(1024)
            if not chunk:
                break
            buf += chunk
        return buf.split(b"\r\n", 1)[0].decode()

    # --- framing ---------------------------------------------------------
    def _send(self, obj):
        payload = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            hdr = struct.pack("!BB", 0x81, 0x80 | n)
        elif n < 65536:
            hdr = struct.pack("!BBH", 0x81, 0x80 | 126, n)
        else:
            hdr = struct.pack("!BBQ", 0x81, 0x80 | 127, n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(hdr + mask + masked)

    def _recv(self, timeout=2.0):
        self.sock.settimeout(timeout)
        header = self._recv_exact(2)
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        data = self._recv_exact(length) if length else b""
        return json.loads(data)

    def _recv_exact(self, n):
        d = b""
        while len(d) < n:
            c = self.sock.recv(n - len(d))
            if not c:
                raise EOFError("connection closed")
            d += c
        return d

    # --- request/response helpers (push disabled, so each is 1:1) --------
    def join(self, capabilities):
        self._send({"action": "join", "capabilities": capabilities})
        return self._recv()

    def poll(self, capabilities=None, result=None, session_token=None):
        msg = {}
        if capabilities is not None:
            msg["capabilities"] = capabilities
        if result is not None:
            msg["result"] = result
        if session_token is not None:
            msg["session_token"] = session_token
        self._send(msg)
        return self._recv()

    def start(self, session_token=None):
        msg = {"action": "start"}
        if session_token is not None:
            msg["session_token"] = session_token
        self._send(msg)
        return self._recv()

    def dismiss(self, session_token=None):
        msg = {"action": "dismiss"}
        if session_token is not None:
            msg["session_token"] = session_token
        self._send(msg)
        return self._recv()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class RoomServerTestCase(unittest.TestCase):
    """Integration tests: real HTTP + real websocket against a live server."""

    @classmethod
    def setUpClass(cls):
        # Disable periodic state push so websocket exchanges are deterministic
        # request/response pairs.
        room_server._WS_PUSH_INTERVAL = 3600

        room_server.rooms.clear()
        for room_id in range(1, 6):
            room_server.rooms[room_id] = _make_room(room_id)

        cls.server = ThreadingHTTPServer((TEST_HOST, TEST_PORT), room_server.RoomRequestHandler)
        cls.server_thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.2)

        cls.client = RoomClient(server_url=BASE_URL)
        assert cls.client.available(), "requests library not available — install it to run tests"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _ws(self, room_id, badge_id):
        """Open a WSClient and auto-close it at test teardown.

        Ensures the room exists first — closing a websocket now deletes the
        room when its last badge leaves, so rooms don't persist across tests.
        """
        if room_id not in room_server.rooms:
            room_server.rooms[room_id] = _make_room(room_id)
        c = WSClient(room_id, badge_id)
        self.addCleanup(c.close)
        return c

    def _get_json(self, path):
        url = BASE_URL + path
        with urllib.request.urlopen(url) as resp:
            content = resp.read().decode("utf-8")
            if resp.headers.get("Content-Type", "").startswith("application/json"):
                return json.loads(content), resp.code
            return content, resp.code

    def _admin_get_json(self, path):
        url = BASE_URL + path
        creds = base64.b64encode(("admin:" + room_server._ADMIN_PASSWORD).encode()).decode()
        req = urllib.request.Request(url, headers={"Authorization": "Basic " + creds})
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode("utf-8")
            if resp.headers.get("Content-Type", "").startswith("application/json"):
                return json.loads(content), resp.code
            return content, resp.code

    # ------------------------------------------------------------------ join

    def test_join_room_full_returns_error(self):
        room_server.rooms[99] = _make_room(99)
        conns = []
        try:
            for i in range(MAX_BADGES):
                c = WSClient(99, "badge-full-{}".format(i))
                conns.append(c)
                state = c.join(GPS_CAPS)
                self.assertNotIn("error", state)
            overflow = WSClient(99, "badge-overflow")
            conns.append(overflow)
            state = overflow.join(GPS_CAPS)
            self.assertIn("error", state)
        finally:
            for c in conns:
                c.close()
            room_server.rooms.pop(99, None)

    def test_join_returns_expected_fields(self):
        state = self._ws(1, "badge-join").join(GPS_CAPS)
        self.assertNotIn("error", state)
        self.assertEqual(state["room_id"], 1)
        self.assertIn("assignment", state)
        self.assertIn("display", state)
        self.assertIn("colour", state)
        self.assertIn("room_state", state)
        self.assertIsNotNone(state["session_token"])

    def test_join_assigns_a_colour(self):
        state = self._ws(1, "badge-colour").join(GPS_CAPS)
        self.assertIn(state["colour"], COLOURS)

    def test_join_multiple_capabilities(self):
        state = self._ws(1, "badge-multicap").join(GPS_CAPS + MEGADRIVE_CAPS)
        self.assertNotIn("error", state)
        self.assertFalse(state["need_capabilities"])

    # ------------------------------------------------------------------ poll

    def test_poll_after_join(self):
        c = self._ws(1, "badge-poll")
        token = c.join(GPS_CAPS)["session_token"]
        state = c.poll(GPS_CAPS, session_token=token)
        self.assertIn("assignment", state)
        self.assertIn("scores", state)

    def test_poll_assignment_is_stable(self):
        c = self._ws(1, "badge-stable")
        join_data = c.join(GPS_CAPS)
        poll_data = c.poll(GPS_CAPS, session_token=join_data["session_token"])
        if join_data.get("assignment") and poll_data.get("assignment"):
            self.assertEqual(join_data["assignment"]["id"], poll_data["assignment"]["id"])

    def test_waiting_room_has_no_assignment(self):
        room_server.rooms[2] = _make_room(2)
        state = self._ws(2, "badge-waiting").join(GPS_CAPS)
        self.assertEqual(state["room_state"], "waiting")
        self.assertIsNone(state["assignment"])

    # ------------------------------------------------------------------ start

    def test_start_round_transitions_to_in_round(self):
        room_server.rooms[3] = _make_room(3)
        c = self._ws(3, "badge-start")
        token = c.join(GPS_CAPS)["session_token"]
        state = c.start(session_token=token)
        self.assertEqual(state["room_state"], "in-round")

    def test_poll_after_start_returns_assignment(self):
        room_server.rooms[3] = _make_room(3)
        c = self._ws(3, "badge-start2")
        token = c.join(GPS_CAPS)["session_token"]
        state = c.start(session_token=token)
        self.assertIsNotNone(state["assignment"])

    # ------------------------------------------------------------------ result

    def test_submit_passed_increments_score(self):
        room_server.rooms[2] = _make_room(2)
        c = self._ws(2, "badge-pass")
        token = c.join(GPS_CAPS)["session_token"]
        state = c.start(session_token=token)
        assignment = state.get("assignment")
        if assignment is None:
            self.skipTest("No assignment issued — cannot test result submission")
        before = state["scores"]["passed"]
        result = {
            "assignment_id": assignment["id"],
            "status": "passed",
            "module": assignment["module"],
            "command": assignment["command"],
        }
        state = c.poll(GPS_CAPS, result=result, session_token=token)
        self.assertEqual(state["scores"]["passed"], before + 1)

    def test_submit_failed_increments_score(self):
        room_server.rooms[2] = _make_room(2)
        c = self._ws(2, "badge-fail")
        token = c.join(GPS_CAPS)["session_token"]
        state = c.start(session_token=token)
        assignment = state.get("assignment")
        if assignment is None:
            self.skipTest("No assignment issued — cannot test result submission")
        before = state["scores"]["failed"]
        result = {
            "assignment_id": assignment["id"],
            "status": "failed",
            "module": assignment["module"],
            "command": assignment["command"],
        }
        state = c.poll(GPS_CAPS, result=result, session_token=token)
        self.assertEqual(state["scores"]["failed"], before + 1)

    def test_wrong_assignment_id_is_ignored(self):
        room_server.rooms[2] = _make_room(2)
        c = self._ws(2, "badge-wrongid")
        token = c.join(GPS_CAPS)["session_token"]
        state = c.start(session_token=token)
        before = state["scores"]["passed"]
        result = {
            "assignment_id": "does-not-exist",
            "status": "passed",
            "module": "GPS",
            "command": "move 5m away",
        }
        state = c.poll(GPS_CAPS, result=result, session_token=token)
        self.assertEqual(state["scores"]["passed"], before)

    # ------------------------------------------------------------------ dismiss

    def test_dismiss_after_round_returns_to_waiting(self):
        room_server.rooms[2] = _make_room(2)
        c = self._ws(2, "badge-dismiss")
        token = c.join(GPS_CAPS)["session_token"]
        c.start(session_token=token)
        # Force the round to end via the admin "hurry" control, then wait it out.
        creds = base64.b64encode(("admin:" + room_server._ADMIN_PASSWORD).encode()).decode()
        req = urllib.request.Request(
            BASE_URL + "/api/rooms/2/hurry", data=b"{}",
            headers={"Content-Type": "application/json", "Authorization": "Basic " + creds},
            method="POST",
        )
        urllib.request.urlopen(req).close()
        # hurry leaves 5s on the clock, so the round needs a few seconds to end.
        self.assertTrue(_wait_until(
            lambda: c.poll(session_token=token).get("room_state") == "finished", timeout=8.0
        ))
        state = c.dismiss(session_token=token)
        self.assertEqual(state["room_state"], "waiting")

    # ------------------------------------------------------------------ leave

    def test_leave_removes_badge(self):
        room_server.rooms[3] = _make_room(3)
        c = WSClient(3, "badge-leave")
        c.join(GPS_CAPS)
        self.assertTrue(_wait_until(lambda: 3 in room_server.rooms))
        c.close()  # closing the socket is the leave
        self.assertTrue(_wait_until(lambda: 3 not in room_server.rooms))

    def test_room_deleted_when_last_badge_leaves(self):
        room_server.rooms[5] = _make_room(5)
        c = WSClient(5, "badge-reset")
        c.join(GPS_CAPS)
        c.close()
        self.assertTrue(_wait_until(lambda: 5 not in room_server.rooms))
        # A fresh connection to the now-deleted room is rejected at handshake.
        with self.assertRaises(WSHandshakeError):
            WSClient(5, "badge-reset")

    def test_handshake_without_join_keeps_room(self):
        # A connection that completes the handshake but never joins (a probe, an
        # early drop) must NOT delete the room when it disconnects.
        room_server.rooms[7] = _make_room(7)
        self.addCleanup(lambda: room_server.rooms.pop(7, None))
        c = WSClient(7, "badge-probe")
        c.close()  # never sent a join frame
        # The room must still be there after the disconnect handler runs; assert
        # it stays present rather than racing on a never-happening deletion.
        self.assertFalse(_wait_until(lambda: 7 not in room_server.rooms, timeout=0.5))

    # ------------------------------------------------------------------ errors

    def test_start_without_join_returns_error(self):
        # start_round's error must be surfaced, not masked by a poll snapshot.
        room_server.rooms[8] = _make_room(8)
        self.addCleanup(lambda: room_server.rooms.pop(8, None))
        c = self._ws(8, "badge-nostart")
        state = c.start()
        self.assertIn("error", state)


    def test_invalid_room_id_returns_error(self):
        with self.assertRaises(WSHandshakeError):
            WSClient(999, "badge-bad")

    def test_missing_badge_id_returns_400(self):
        status = _ws_status_for("/ws/rooms/1")
        self.assertIn("400", status)

    # ------------------------------------------------------------------ stats

    def test_stats_endpoint_returns_total_games(self):
        response, status = self._get_json("/api/stats")
        self.assertEqual(status, 200)
        self.assertIn("total_games", response)
        self.assertIsInstance(response["total_games"], int)

    # ------------------------------------------------------------------ admin

    def test_admin_status_shape(self):
        response, status = self._admin_get_json("/api/admin/status")
        self.assertEqual(status, 200)
        self.assertIn("rooms", response)
        self.assertIn("total_badges", response)
        self.assertIsInstance(response["rooms"], list)

    def test_create_room_reuses_deleted_id(self):
        data1, _ = self.client.create_room()
        room_id = data1["room_id"]
        c = WSClient(room_id, "badge-reuse")
        c.join(GPS_CAPS)
        c.close()
        self.assertTrue(_wait_until(lambda: room_id not in room_server.rooms))
        data2, _ = self.client.create_room()
        self.assertEqual(data2["room_id"], room_id)

    def test_create_room(self):
        initial_count = len(room_server.rooms)
        data, error = self.client.create_room()
        self.assertIsNone(error)
        self.assertIn("room_id", data)
        self.assertEqual(len(room_server.rooms), initial_count + 1)
        room_id = data["room_id"]
        state = self._ws(room_id, "badge-create-test").join(GPS_CAPS)
        self.assertEqual(state["room_id"], room_id)

    def test_list_rooms_returns_active_rooms(self):
        room_server.rooms[4] = _make_room(4)
        c = self._ws(4, "badge-list-test")
        c.join(GPS_CAPS)
        response, status = self._get_json("/api/rooms")
        self.assertEqual(status, 200)
        self.assertIn("rooms", response)
        room_ids = [r["room_id"] for r in response["rooms"]]
        self.assertIn(4, room_ids)
        for r in response["rooms"]:
            self.assertIn("badge_count", r)
            self.assertIn("room_state", r)
            self.assertGreater(r["badge_count"], 0)

    def test_admin_page_returns_html(self):
        response, status = self._admin_get_json("/admin")
        self.assertEqual(status, 200)
        self.assertIsInstance(response, str)
        self.assertIn("html", response.lower())

    def test_multiple_badges_visible_in_admin(self):
        room_id = 4
        room_server.rooms[room_id] = _make_room(room_id)
        conns = [self._ws(room_id, "multi-{}".format(i)) for i in range(2)]
        for c in conns:
            c.join(GPS_CAPS)
        response, _ = self._admin_get_json("/api/admin/status")
        room_4 = next(r for r in response["rooms"] if r["room_id"] == room_id)
        self.assertGreaterEqual(room_4["badge_count"], 2)


class WSDeltaTestCase(unittest.TestCase):
    """Unit tests for the websocket delta projection / diff helpers."""

    def _state(self, **kw):
        base = {
            "room_state": "in-round",
            "time_remaining_s": 119.4,
            "scores": {"passed": 0, "failed": 0},
            "assignment": {"id": "a1", "module": "GPS", "command": "go",
                           "time_remaining_s": 14.8, "timeout_s": 15.0},
            "display": None,
            "colour": "red",
        }
        base.update(kw)
        return base

    def test_first_send_is_full(self):
        state = self._state()
        payload, comparable = room_server._ws_state_delta(state, None)
        self.assertEqual(payload, state)
        self.assertIsNotNone(comparable)

    def test_no_change_yields_empty_delta(self):
        state = self._state()
        _, comparable = room_server._ws_state_delta(state, None)
        payload, _ = room_server._ws_state_delta(state, comparable)
        self.assertEqual(payload, {})

    def test_round_timer_never_in_generic_delta(self):
        # The round timer is handled by _ws_timer_anchor, never the comparable.
        _, comparable = room_server._ws_state_delta(self._state(time_remaining_s=119.4), None)
        payload, _ = room_server._ws_state_delta(self._state(time_remaining_s=80.0), comparable)
        self.assertNotIn("time_remaining_s", payload)

    def test_timer_anchor_first_send(self):
        send, anchor = room_server._ws_timer_anchor(120.0, None, now=1000.0)
        self.assertTrue(send)
        self.assertEqual(anchor, (120.0, 1000.0))

    def test_timer_anchor_linear_tick_is_silent(self):
        _, anchor = room_server._ws_timer_anchor(120.0, None, now=1000.0)
        # 2s later the timer has dropped ~2s — exactly as predicted -> no send.
        send, anchor2 = room_server._ws_timer_anchor(118.0, anchor, now=1002.0)
        self.assertFalse(send)
        self.assertEqual(anchor2, anchor)

    def test_timer_anchor_jump_is_sent(self):
        _, anchor = room_server._ws_timer_anchor(120.0, None, now=1000.0)
        # admin "hurry": 1s later the timer is 5s, not ~119 -> jump -> send.
        send, anchor2 = room_server._ws_timer_anchor(5.0, anchor, now=1001.0)
        self.assertTrue(send)
        self.assertEqual(anchor2, (5.0, 1001.0))

    def test_timer_anchor_none_resets(self):
        _, anchor = room_server._ws_timer_anchor(120.0, None, now=1000.0)
        send, anchor2 = room_server._ws_timer_anchor(None, anchor, now=1005.0)
        self.assertFalse(send)
        self.assertIsNone(anchor2)

    def test_assignment_timer_tick_is_silent(self):
        # Only the assignment's internal timer changed -> dropped from compare.
        a1 = {"id": "a1", "module": "GPS", "command": "go",
              "time_remaining_s": 14.8, "timeout_s": 15.0}
        a2 = dict(a1, time_remaining_s=9.2)
        _, comparable = room_server._ws_state_delta(self._state(assignment=a1), None)
        payload, _ = room_server._ws_state_delta(self._state(assignment=a2), comparable)
        self.assertNotIn("assignment", payload)

    def test_new_assignment_is_sent(self):
        a1 = {"id": "a1", "module": "GPS", "command": "go",
              "time_remaining_s": 14.8, "timeout_s": 15.0}
        a2 = dict(a1, id="a2", command="stop")
        _, comparable = room_server._ws_state_delta(self._state(assignment=a1), None)
        payload, _ = room_server._ws_state_delta(self._state(assignment=a2), comparable)
        self.assertIn("assignment", payload)
        self.assertEqual(payload["assignment"]["id"], "a2")

    def test_score_change_only_sends_scores(self):
        _, comparable = room_server._ws_state_delta(self._state(), None)
        payload, _ = room_server._ws_state_delta(
            self._state(scores={"passed": 1, "failed": 0}), comparable)
        self.assertEqual(set(payload), {"scores"})

    def test_delta_detects_in_place_score_mutation(self):
        # The room returns its live _scores dict by reference; the comparable
        # must snapshot it, or mutating that same dict in place (as scoring
        # does) would be invisible to the diff.
        scores = {"passed": 0, "failed": 0}
        _, comparable = room_server._ws_state_delta(self._state(scores=scores), None)
        scores["passed"] = 1  # mutate the SAME object the room would
        payload, _ = room_server._ws_state_delta(self._state(scores=scores), comparable)
        self.assertIn("scores", payload)
        self.assertEqual(payload["scores"]["passed"], 1)

    def test_delta_detects_in_place_badge_score_mutation(self):
        inner = {"passed": 0, "failed": 0}
        badge_scores = {"red": inner}
        _, comparable = room_server._ws_state_delta(self._state(badge_scores=badge_scores), None)
        inner["passed"] = 2  # mutate nested live score dict in place
        payload, _ = room_server._ws_state_delta(self._state(badge_scores=badge_scores), comparable)
        self.assertIn("badge_scores", payload)
        self.assertEqual(payload["badge_scores"]["red"]["passed"], 2)


if __name__ == "__main__":
    unittest.main()
