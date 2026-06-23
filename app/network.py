import asyncio
import json
import time

from app_components import Notification


class _CapabilitiesSync:
	def __init__(self):
		self._dirty = True
		self._last_sent = None

	def maybe_caps(self, current):
		if self._dirty or current != self._last_sent:
			return current
		return None

	def mark_sent(self, caps):
		self._dirty = False
		self._last_sent = caps

	def mark_dirty(self):
		self._dirty = True


class NetworkController:
	def __init__(self, app):
		self.app = app
		self.outbox = []
		self.alive = False
		self.joined = False
		self.caps_sync = _CapabilitiesSync()

	def mark_caps_dirty(self):
		self.caps_sync.mark_dirty()

	def reset_outbox(self):
		self.outbox = []

	def queue_action(self, action):
		# Button handlers run on the same asyncio loop as run(), so a plain list
		# is a safe outbox — the ws loop drains it on its next tick.
		self.outbox.append({"action": action})

	def _capabilities(self):
		return self.app.module_registry.get_capabilities()

	async def run(self):
		print("[RC] background_task starting")
		while True:
			if self.app.session.in_game:
				print("[RC] ws: starting session for room {}".format(self.app.session.room_id))
				await self._run_ws_session()
				if self.app.session.in_game:
					print("[RC] ws: disconnected, retrying in 2s")
					await asyncio.sleep(2)
			else:
				await asyncio.sleep(0.1)

	async def _run_ws_session(self):
		# The server pushes deltas only when state changes, so it can go quiet
		# for long stretches. We therefore split the session into two coroutines:
		# a reader that blocks on incoming frames (never cancelled mid-frame, so
		# the stream can't desync) and a writer that flushes queued button
		# actions / results every tick regardless of whether the server is
		# talking. On teardown the reader is cancelled and the socket closed.
		session = self.app.session
		ws_url = self.app.room_client.ws_url(session.room_id)
		print("[RC] ws → {}".format(ws_url))
		ws = None
		reader_task = None
		try:
			ws = await self.app.room_client.connect_ws(ws_url)
			self.alive = True
			self.joined = False
			print("[RC] ws connected")

			# Join over the websocket. The join carries our secret_id, which
			# authenticates the connection: the server derives our badge_id from
			# it and binds it to this socket for the rest of the session. The
			# reply gives us our colour and the initial room state.
			caps = self._capabilities()
			await self._ws_send_json(ws, {
				"action": "join",
				"capabilities": caps,
				"secret_id": self.app._secret_id,
			})
			self.caps_sync.mark_sent(caps)

			reader_task = asyncio.create_task(self._ws_read_loop(ws))
			while session.in_game and self.alive:
				await self._flush_ws_outbox(ws)
				await asyncio.sleep(0.1)
		except Exception as exc:
			print("[RC] ws error: {}".format(exc))
		finally:
			self.alive = False
			if reader_task is not None:
				reader_task.cancel()
				try:
					await reader_task
				except Exception:
					pass
			if ws is not None:
				try:
					await ws.close()
				except Exception:
					pass
			self.outbox = []
			print("[RC] ws session ended")

	async def _ws_read_loop(self, ws):
		try:
			while True:
				opcode, data = await ws.ws.receive()
				if opcode == ws.ws.CLOSE:
					print("[RC] ws RECV <close>")
					break
				if not isinstance(data, str) or not data:
					continue
				print("[RC] ws RECV {}".format(data))
				self._apply_ws_state(json.loads(data))
		except asyncio.CancelledError:
			pass
		except Exception as exc:
			print("[RC] ws read error: {}".format(exc))
		finally:
			self.alive = False

	async def _ws_send_json(self, ws, obj):
		# Single choke point for outbound frames so every message is logged.
		print("[RC] ws SEND {}".format(json.dumps(obj)))
		await ws.send_json(obj)

	async def _flush_ws_outbox(self, ws):
		# Queued button actions (start/dismiss), then a capability update if
		# dirty, then a pending result. The result is captured-and-cleared
		# before awaiting the send so a result set by update() during the await
		# is preserved for the next flush rather than dropped or double-sent.
		session = self.app.session
		while self.outbox:
			msg = self.outbox.pop(0)
			await self._ws_send_json(ws, msg)
		caps = self.caps_sync.maybe_caps(self._capabilities())
		if caps is not None:
			self.caps_sync.mark_sent(caps)
			await self._ws_send_json(ws, {"capabilities": caps})
		result = session.pending_result
		if result:
			session.pending_result = None
			await self._ws_send_json(ws, {"result": result})

	def _apply_ws_state(self, data):
		app = self.app
		if "error" in data:
			print("[RC] ws ← server error: {}".format(data["error"]))
			app.notification = Notification(data["error"])
			# An error before we've joined this session is a fatal join failure
			# (e.g. "Room is full" on a reconnect after being pruned): bail back
			# to the menu rather than reconnect-looping on the same rejection.
			# We key off this session's join, not any earlier connection.
			if not self.joined:
				app.session.stop_room()
				app._ensure_menu()
			return
		self.joined = True
		if data.get("need_capabilities"):
			self.caps_sync.mark_dirty()
		new_colour = app.session.apply_poll_response(
			data,
			now_ms=time.ticks_ms(),
			module_lookup=app._module_by_name,
		)
		if new_colour:
			app._set_leds(new_colour)
