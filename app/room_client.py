import json
import time


DEFAULT_SERVER_URL = "https://racecondition.area.io"
# DEFAULT_SERVER_URL = "http://archaix.lan:8000"
REQUEST_TIMEOUT_SECONDS = 3


def _make_ssl_context():
    # This firmware's asyncio.open_connection does `import ssl` when handed
    # ssl=True, but the TLS module here is the built-in `tls` (mbedtls); the
    # `ssl` compat shim isn't frozen in. So we build the context ourselves and
    # pass the object — open_connection then calls ctx.wrap_socket() and never
    # touches the missing `ssl` module. Certs aren't verified (no CA bundle on
    # the badge); SNI is still sent, which Fly's edge needs for routing.
    for modname in ("tls", "ssl"):
        try:
            mod = __import__(modname)
        except ImportError:
            continue
        ctx = mod.SSLContext(mod.PROTOCOL_TLS_CLIENT)
        try:
            ctx.check_hostname = False
        except Exception:
            pass
        try:
            ctx.verify_mode = mod.CERT_NONE
        except Exception:
            pass
        print("[RC:net] TLS context via '{}'".format(modname))
        return ctx
    raise RuntimeError("no TLS module (tls/ssl) available")


class RoomClient:
    def __init__(self, server_url=DEFAULT_SERVER_URL):
        self.server_url = server_url.rstrip("/")
        self._requests = None
        self._import_error = None
        try:
            import requests  # type: ignore
            self._requests = requests
            print("[RC:net] requests loaded (sync)")
        except ImportError as exc:
            self._import_error = str(exc)
            print("[RC:net] requests unavailable: {}".format(exc))
        try:
            from . import aiohttp_ws as _ws  # type: ignore  # noqa: F401
            print("[RC:net] aiohttp_ws loaded")
        except ImportError as exc:
            print("[RC:net] aiohttp_ws unavailable: {}".format(exc))

    def available(self):
        return self._requests is not None

    def _timestamp_ms(self):
        try:
            return time.ticks_ms()
        except AttributeError:
            return int(time.time() * 1000)

    def ws_url(self, room_id, badge_id):
        # The session token is issued by the server in response to the join
        # message sent over the socket, so it isn't needed in the URL.
        base = self.server_url.replace("https://", "wss://").replace("http://", "ws://")
        return "{}/ws/rooms/{}?badge_id={}".format(base, room_id, badge_id)

    async def connect_ws(self, ws_url):
        import asyncio
        from .aiohttp_ws import WebSocketClient, urlparse as ws_urlparse, ClientWebSocketResponse

        uri = ws_urlparse(ws_url)
        if uri is None:
            raise ValueError("Invalid WS URL: {}".format(ws_url))
        # Pass an actual TLS context (not True) so open_connection doesn't try
        # to `import ssl`; None for plain ws://.
        ssl = _make_ssl_context() if uri.protocol == "wss" else None

        # NOTE: aiohttp_ws.handshake() calls this with ssl= as a keyword, so
        # the parameter MUST be named `ssl`. MicroPython 1.28 open_connection
        # accepts ssl=None/False and only wraps in TLS when ssl is truthy.
        async def _request(method, url, ssl, headers, is_handshake, version):
            proto_end = url.find("://") + 3
            host_path = url[proto_end:]
            slash = host_path.find("/")
            path = host_path[slash:] if slash >= 0 else "/"
            print("[RC:net] WS open_connection {}:{}{}".format(
                uri.hostname, uri.port, " (ssl)" if ssl else "",
            ))
            reader, writer = await asyncio.open_connection(uri.hostname, uri.port, ssl=ssl)
            writer.write("{} {} {}\r\n".format(method, path, version).encode())
            for k, v in headers.items():
                writer.write("{}: {}\r\n".format(k, v).encode())
            writer.write(b"\r\n")
            await writer.drain()
            return reader, writer

        client = WebSocketClient({})
        await client.connect(ws_url, ssl=ssl, handshake_request=_request)
        print("[RC:net] WS connected to {}".format(ws_url))
        return ClientWebSocketResponse(client)

    # In-game actions (join/poll/start/dismiss/leave) all flow over the
    # websocket (see RaceConditionApp._run_ws_session). Only room discovery and
    # creation — menu actions, before a game — use these HTTP helpers.

    def list_rooms(self):
        return self._get("/api/rooms")

    def create_room(self):
        return self._post("/api/rooms/create", {})

    def _get(self, path):
        if not self._requests:
            return None, "Networking unavailable: {}".format(self._import_error or "requests not found")
        url = "{}{}".format(self.server_url, path)
        response = None
        try:
            response = self._requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
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
