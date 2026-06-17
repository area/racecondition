#!/usr/bin/env python3
"""
Integration tests for room_server.py.
Runs the server in a background thread; uses RoomClient for all requests.
"""
import time
import unittest
import urllib.request
import urllib.error
from threading import Thread
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys

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

from leaderboard import SqliteLeaderboard


def _make_room(room_id):
    return Room(room_id, leaderboard=SqliteLeaderboard(":memory:"))

TEST_HOST = "127.0.0.1"
TEST_PORT = 18000
BASE_URL = "http://{}:{}".format(TEST_HOST, TEST_PORT)

GPS_CAPS = [{"module": "GPS", "commands": ["move 5m away"]}]
MEGADRIVE_CAPS = [{"module": "MegaDrive", "commands": ["a", "b"]}]


class RoomServerTestCase(unittest.TestCase):
    """Integration tests: real HTTP, real client, real server."""

    @classmethod
    def setUpClass(cls):
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

    def _get_json(self, path):
        url = BASE_URL + path
        with urllib.request.urlopen(url) as resp:
            import json
            content = resp.read().decode("utf-8")
            if resp.headers.get("Content-Type", "").startswith("application/json"):
                return json.loads(content), resp.code
            return content, resp.code

    # ------------------------------------------------------------------ join

    def test_join_room_full_returns_error(self):
        from room import MAX_BADGES
        room_server.rooms[99] = _make_room(99)
        try:
            for i in range(MAX_BADGES):
                data, error = self.client.join_room(99, "badge-full-{}".format(i), GPS_CAPS)
                self.assertIsNone(error)
            data, error = self.client.join_room(99, "badge-overflow", GPS_CAPS)
            self.assertIsNone(data)
            self.assertIsNotNone(error)
        finally:
            room_server.rooms.pop(99, None)

    def test_join_returns_expected_fields(self):
        data, error = self.client.join_room(1, "badge-join", GPS_CAPS)
        self.assertIsNone(error)
        self.assertEqual(data["room_id"], 1)
        self.assertIn("assignment", data)
        self.assertIn("display", data)
        self.assertIn("colour", data)
        self.assertIn("room_state", data)

    def test_join_assigns_a_colour(self):
        data, error = self.client.join_room(1, "badge-colour", GPS_CAPS)
        self.assertIsNone(error)
        from room import COLOURS
        self.assertIn(data["colour"], COLOURS)

    def test_join_multiple_capabilities(self):
        caps = GPS_CAPS + MEGADRIVE_CAPS
        data, error = self.client.join_room(1, "badge-multicap", caps)
        self.assertIsNone(error)
        self.assertIsNotNone(data)

    # ------------------------------------------------------------------ poll

    def test_poll_after_join(self):
        badge_id = "badge-poll"
        self.client.join_room(1, badge_id, GPS_CAPS)
        data, error = self.client.poll(1, badge_id, GPS_CAPS)
        self.assertIsNone(error)
        self.assertIn("assignment", data)
        self.assertIn("scores", data)

    def test_poll_assignment_is_stable(self):
        """Same assignment should be returned on repeated polls."""
        badge_id = "badge-stable"
        join_data, _ = self.client.join_room(1, badge_id, GPS_CAPS)
        poll_data, _ = self.client.poll(1, badge_id, GPS_CAPS)
        if join_data.get("assignment") and poll_data.get("assignment"):
            self.assertEqual(join_data["assignment"]["id"], poll_data["assignment"]["id"])

    def test_waiting_room_has_no_assignment(self):
        """Badges in waiting state should not receive assignments."""
        badge_id = "badge-waiting"
        room_server.rooms[2] = _make_room(2)
        data, error = self.client.join_room(2, badge_id, GPS_CAPS)
        self.assertIsNone(error)
        self.assertEqual(data["room_state"], "waiting")
        self.assertIsNone(data["assignment"])

    # ------------------------------------------------------------------ start

    def test_start_round_transitions_to_in_round(self):
        import json, urllib.request
        room_server.rooms[3] = _make_room(3)
        self.client.join_room(3, "badge-start", GPS_CAPS)
        url = BASE_URL + "/api/rooms/3/start"
        req = urllib.request.Request(
            url,
            data=json.dumps({"badge_id": "badge-start"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
        self.assertEqual(data["room_state"], "in-round")

    def test_poll_after_start_returns_assignment(self):
        import json, urllib.request
        room_server.rooms[3] = _make_room(3)
        self.client.join_room(3, "badge-start2", GPS_CAPS)
        url = BASE_URL + "/api/rooms/3/start"
        req = urllib.request.Request(
            url,
            data=json.dumps({"badge_id": "badge-start2"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req).close()
        data, error = self.client.poll(3, "badge-start2", GPS_CAPS)
        self.assertIsNone(error)
        self.assertIsNotNone(data["assignment"])

    # ------------------------------------------------------------------ result

    def test_submit_passed_increments_score(self):
        room_server.rooms[2] = _make_room(2)
        badge_id = "badge-pass"
        import json, urllib.request
        join_data, _ = self.client.join_room(2, badge_id, GPS_CAPS)
        session_token = join_data["session_token"]
        url = BASE_URL + "/api/rooms/2/start"
        req = urllib.request.Request(
            url,
            data=json.dumps({"badge_id": badge_id}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req).close()
        poll_data, _ = self.client.poll(2, badge_id, GPS_CAPS, session_token=session_token)
        score_before = poll_data["scores"]["passed"]
        assignment = poll_data.get("assignment")
        if assignment is None:
            self.skipTest("No assignment issued — cannot test result submission")

        result = {
            "assignment_id": assignment["id"],
            "status": "passed",
            "module": assignment["module"],
            "command": assignment["command"],
        }
        data, error = self.client.poll(2, badge_id, GPS_CAPS, result=result, session_token=session_token)
        self.assertIsNone(error)
        self.assertEqual(data["scores"]["passed"], score_before + 1)

    def test_submit_failed_increments_score(self):
        room_server.rooms[2] = _make_room(2)
        badge_id = "badge-fail"
        import json, urllib.request
        join_data, _ = self.client.join_room(2, badge_id, GPS_CAPS)
        session_token = join_data["session_token"]
        url = BASE_URL + "/api/rooms/2/start"
        req = urllib.request.Request(
            url,
            data=json.dumps({"badge_id": badge_id}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req).close()
        poll_data, _ = self.client.poll(2, badge_id, GPS_CAPS, session_token=session_token)
        score_before = poll_data["scores"]["failed"]
        assignment = poll_data.get("assignment")
        if assignment is None:
            self.skipTest("No assignment issued — cannot test result submission")

        result = {
            "assignment_id": assignment["id"],
            "status": "failed",
            "module": assignment["module"],
            "command": assignment["command"],
        }
        data, error = self.client.poll(2, badge_id, GPS_CAPS, result=result, session_token=session_token)
        self.assertIsNone(error)
        self.assertEqual(data["scores"]["failed"], score_before + 1)

    def test_wrong_assignment_id_is_ignored(self):
        room_server.rooms[2] = _make_room(2)
        badge_id = "badge-wrongid"
        import json, urllib.request
        self.client.join_room(2, badge_id, GPS_CAPS)
        url = BASE_URL + "/api/rooms/2/start"
        req = urllib.request.Request(
            url,
            data=json.dumps({"badge_id": badge_id}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req).close()
        join_data, _ = self.client.poll(2, badge_id, GPS_CAPS)
        score_before = join_data["scores"]["passed"]
        result = {
            "assignment_id": "does-not-exist",
            "status": "passed",
            "module": "GPS",
            "command": "move 5m away",
        }
        data, error = self.client.poll(2, badge_id, GPS_CAPS, result=result)
        self.assertIsNone(error)
        self.assertEqual(data["scores"]["passed"], score_before)

    # ------------------------------------------------------------------ leave

    def test_leave_room(self):
        badge_id = "badge-leave"
        self.client.join_room(3, badge_id, GPS_CAPS)
        data, error = self.client.leave_room(3, badge_id)
        self.assertIsNone(error)
        self.assertEqual(data["status"], "left")

    def test_room_deleted_when_last_badge_leaves(self):
        room_server.rooms[5] = _make_room(5)
        badge_id = "badge-reset"
        self.client.join_room(5, badge_id, GPS_CAPS)
        self.client.leave_room(5, badge_id)
        self.assertNotIn(5, room_server.rooms)
        data, error = self.client.join_room(5, badge_id, GPS_CAPS)
        self.assertIsNone(data)
        self.assertIsNotNone(error)

    # ------------------------------------------------------------------ errors

    def test_invalid_room_id_returns_error(self):
        data, error = self.client.join_room(999, "badge-bad", GPS_CAPS)
        self.assertIsNone(data)
        self.assertIsNotNone(error)

    def test_missing_badge_id_returns_error(self):
        import json, urllib.request, urllib.error
        url = BASE_URL + "/api/rooms/1/join"
        req = urllib.request.Request(
            url,
            data=json.dumps({"capabilities": []}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)

    def test_invalid_json_returns_400(self):
        import urllib.request, urllib.error
        url = BASE_URL + "/api/rooms/1/join"
        req = urllib.request.Request(
            url,
            data=b"not valid json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)

    # ------------------------------------------------------------------ stats

    def test_stats_endpoint_returns_total_games(self):
        response, status = self._get_json("/api/stats")
        self.assertEqual(status, 200)
        self.assertIn("total_games", response)
        self.assertIsInstance(response["total_games"], int)

    # ------------------------------------------------------------------ admin

    def test_admin_status_shape(self):
        response, status = self._get_json("/api/admin/status")
        self.assertEqual(status, 200)
        self.assertIn("rooms", response)
        self.assertIn("total_badges", response)
        self.assertIsInstance(response["rooms"], list)

    def test_create_room_reuses_deleted_id(self):
        data1, _ = self.client.create_room()
        room_id = data1["room_id"]
        self.client.join_room(room_id, "badge-reuse", GPS_CAPS)
        self.client.leave_room(room_id, "badge-reuse")
        self.assertNotIn(room_id, room_server.rooms)
        data2, _ = self.client.create_room()
        self.assertEqual(data2["room_id"], room_id)

    def test_create_room(self):
        initial_count = len(room_server.rooms)
        data, error = self.client.create_room()
        self.assertIsNone(error)
        self.assertIn("room_id", data)
        self.assertEqual(len(room_server.rooms), initial_count + 1)
        room_id = data["room_id"]
        join_data, join_error = self.client.join_room(room_id, "badge-create-test", GPS_CAPS)
        self.assertIsNone(join_error)
        self.assertEqual(join_data["room_id"], room_id)

    def test_list_rooms_returns_active_rooms(self):
        room_server.rooms[4] = _make_room(4)
        self.client.join_room(4, "badge-list-test", GPS_CAPS)
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
        response, status = self._get_json("/admin")
        self.assertEqual(status, 200)
        self.assertIsInstance(response, str)
        self.assertIn("html", response.lower())

    def test_multiple_badges_visible_in_admin(self):
        room_id = 4
        room_server.rooms[room_id] = Room(room_id)
        for i in range(2):
            self.client.join_room(room_id, "multi-{}".format(i), GPS_CAPS)

        response, _ = self._get_json("/api/admin/status")
        room_4 = next(r for r in response["rooms"] if r["room_id"] == room_id)
        self.assertGreaterEqual(room_4["badge_count"], 2)


if __name__ == "__main__":
    unittest.main()
