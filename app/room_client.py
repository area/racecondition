import json
import time


DEFAULT_SERVER_URL = "http://192.168.1.176:8000"
REQUEST_TIMEOUT_SECONDS = 3


class RoomClient:
    def __init__(self, server_url=DEFAULT_SERVER_URL):
        self.server_url = server_url.rstrip("/")
        self._requests = None
        self._import_error = None
        try:
            import requests  # type: ignore
            self._requests = requests
        except ImportError as exc:
            self._import_error = str(exc)

    def available(self):
        return self._requests is not None

    def join_room(self, room_id, badge_id, capabilities):
        payload = {
            "badge_id": badge_id,
            "capabilities": capabilities,
            "timestamp_ms": time.ticks_ms(),
        }
        return self._post("/api/rooms/{}/join".format(room_id), payload)

    def poll(self, room_id, badge_id, capabilities, result=None):
        payload = {
            "badge_id": badge_id,
            "capabilities": capabilities,
            "result": result,
            "timestamp_ms": time.ticks_ms(),
        }
        return self._post("/api/rooms/{}/poll".format(room_id), payload)

    def leave_room(self, room_id, badge_id):
        payload = {
            "badge_id": badge_id,
            "timestamp_ms": time.ticks_ms(),
        }
        return self._post("/api/rooms/{}/leave".format(room_id), payload)

    def _post(self, path, payload):
        if not self._requests:
            return None, "Networking unavailable: {}".format(self._import_error or "requests not found")
        url = "{}{}".format(self.server_url, path)
        response = None
        try:
            response = self._requests.post(
                url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            data = response.json()
            if response.status_code >= 400:
                return None, data.get("error", "HTTP {}".format(response.status_code))
            return data, None
        except Exception as exc:
            return None, str(exc)
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
