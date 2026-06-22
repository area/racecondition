import app
import asyncio
import json
import time

from machine import I2C

from app_components import Menu, clear_background, Notification
from events.input import Buttons, ButtonDownEvent, ButtonUpEvent
from system.eventbus import eventbus
from system.hexpansion.events import HexpansionRemovalEvent, HexpansionInsertionEvent
from system.hexpansion.util import read_hexpansion_header, detect_eeprom_addr
from system.patterndisplay.events import PatternDisable
from tildagonos import tildagonos

from .hexpansion_names import get_friendly_name
from .hexpansion import ModuleRegistry, CommandStatus
from .room_client import RoomClient
from .session import GameSession
from .test_session import TestSession

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


BADGE_COLOURS = {
    "red":    (40,  0,  0),
    "cyan":   ( 0, 30, 30),
    "blue":   ( 0,  0, 40),
    "yellow": (30, 30,  0),
    "purple": (25,  0, 25),
    "orange": (40, 15,  0),
}

CANCEL_HOLD_MS = 4000


def _build_secret_id():
	try:
		from tildagon import HMAC
		return HMAC.digest(HMAC.HMAC_KEY1, b"Race Condition").hex()
	except Exception:
		pass
	try:
		import machine
		import ubinascii
		return ubinascii.hexlify(machine.unique_id()).decode("utf-8")
	except Exception:
		return "badge-{}".format(time.ticks_ms())


def _derive_public_id(secret_id):
	import uhashlib
	import ubinascii
	h = uhashlib.sha256()
	h.update(secret_id.encode())
	return ubinascii.hexlify(h.digest()[:8]).decode()


class RaceConditionApp(app.App):
	def __init__(self, room_client=None):
		self.button_states = Buttons(self)
		self._secret_id = _build_secret_id()
		self.badge_id = _derive_public_id(self._secret_id)
		self.session = GameSession()
		self.module_registry = ModuleRegistry()
		self.notification = None
		self.room_client = room_client if room_client is not None else RoomClient()
		self.menu = None
		self._room_list = []
		self._cancel_down_event = None
		self._test_session = None
		self._qr_active = False
		self._qr_matrix = None
		self._caps_sync = _CapabilitiesSync()
		self._ws_outbox = []
		self._ws_alive = False
		self._ws_joined = False
		self._scan()
		self._ensure_menu()
		eventbus.emit(PatternDisable())

		eventbus.on(HexpansionInsertionEvent, self._on_insert, self)
		eventbus.on(HexpansionRemovalEvent, self._on_remove, self)
		eventbus.on(ButtonDownEvent, self._on_button_down, self)
		eventbus.on(ButtonUpEvent, self._on_button_up, self)

	def _cleanup(self):
		if self.session.in_game:
			self._leave_room()
		if self.menu:
			self.menu._cleanup()
			self.menu = None
		eventbus.remove(HexpansionInsertionEvent, self._on_insert, self)
		eventbus.remove(HexpansionRemovalEvent, self._on_remove, self)
		eventbus.remove(ButtonDownEvent, self._on_button_down, self)
		eventbus.remove(ButtonUpEvent, self._on_button_up, self)

	def _on_insert(self, event):
		self.notification = Notification(
			"Hexpansion plugged in on port {}".format(event.port),
			port=event.port,
		)
		self._scan()

	def _on_remove(self, event):
		self._scan()

	def _is_cancel(self, event):
		button = getattr(event.button, "parent", None) or event.button
		return button.name.lower() == "cancel"

	def _on_button_down(self, event):
		if self._test_session is not None:
			self._test_session.on_button_down(event)
			return

		if self._qr_active:
			self._exit_qr_screen()
			return

		if self._is_cancel(event):
			if self.session.in_game and self.session.cancel_hold_start is None:
				self.session.cancel_hold_start = time.ticks_ms()
				self._cancel_down_event = event
			return

		if self.session.room_state == "waiting":
			self._start_round()
		elif self.session.room_state == "finished":
			self._dismiss_score()
		elif self.session.in_round and self.session.expected_module:
			self.session.expected_module.on_button_down(event)

	def _on_button_up(self, event):
		if self._test_session is not None:
			self._test_session.on_button_up(event)
			return

		if self._is_cancel(event):
			if (self.session.cancel_hold_start is not None
					and self.session.in_round
					and self.session.expected_module is not None
					and self._cancel_down_event is not None):
				held = time.ticks_diff(time.ticks_ms(), self.session.cancel_hold_start)
				if held < CANCEL_HOLD_MS:
					self.session.expected_module.on_button_down(self._cancel_down_event)
			self.session.cancel_hold_start = None
			self._cancel_down_event = None

	def _main_menu_items(self):
		return ["Join Room", "Create Room", "Set name", "Test modules", "Quit"]

	def _ensure_menu(self):
		if not self.menu:
			self._room_list = []
			self.menu = Menu(
				self,
				self._main_menu_items(),
				select_handler=self._main_menu_select,
				back_handler=self._menu_back,
			)

	def _main_menu_select(self, item, idx):
		if item == "Join Room":
			self._show_join_menu()
		elif item == "Create Room":
			self._do_create_room()
		elif item == "Set name":
			self._show_qr_screen()
		elif item == "Test modules":
			self._start_testing()
		elif item == "Quit":
			self._leave_room()
			self.session.stop_room()
			self.button_states.clear()
			self.minimise()

	def _show_join_menu(self):
		if self.room_client.available():
			data, _ = self.room_client.list_rooms()
			if data:
				self._room_list = data.get("rooms", [])
		if not self._room_list:
			self.notification = Notification("No rooms open")
			return
		if self.menu:
			self.menu._cleanup()
			self.menu = None
		items = []
		for room in self._room_list:
			rid = room["room_id"]
			count = room.get("badge_count", 0)
			state = room.get("room_state", "waiting")
			suffix = " (in-round)" if state == "in-round" else ""
			items.append("Room {} - {}/{}{}".format(
				rid, count, len(BADGE_COLOURS), suffix,
			))
		items.append("Back")
		self.menu = Menu(
			self,
			items,
			select_handler=self._join_menu_select,
			back_handler=self._back_to_main,
		)

	def _join_menu_select(self, item, idx):
		if idx < len(self._room_list):
			self._start_room(self._room_list[idx]["room_id"])
		else:
			self._back_to_main()

	def _back_to_main(self):
		if self.menu:
			self.menu._cleanup()
			self.menu = None

	def _do_create_room(self):
		if not self.room_client.available():
			self.notification = Notification("No network")
			self._ensure_menu()
			return
		data, error = self.room_client.create_room()
		if error:
			self.notification = Notification("Create failed: {}".format(error))
			self._ensure_menu()
			return
		self._start_room(data["room_id"])

	def _menu_back(self):
		pass

	def _scan(self):
		hexpansions = {}
		for port in range(1, 7):
			i2c = I2C(port)
			addr, addr_len = detect_eeprom_addr(i2c)
			if addr is None:
				continue
			header = read_hexpansion_header(i2c, addr, addr_len=addr_len)
			if header is None:
				continue
			friendly_name = get_friendly_name(header.vid, header.pid)
			hexpansions[port] = {
				"name": friendly_name,
				"known": friendly_name is not None,
			}
		self.module_registry.scan(hexpansions)
		self._caps_sync.mark_dirty()
		if self.session.expected_module and self.module_registry.get_by_name(self.session.expected_module.friendly_name()) is None:
			self.session.clear_assignment()

	def _module_by_name(self, module_name):
		return self.module_registry.get_by_name(module_name)

	def _start_room(self, room_id):
		if self.menu:
			self.menu._cleanup()
			self.menu = None
		self.session.start_room(room_id)
		self.module_registry.reset_connected()
		self._caps_sync.mark_dirty()
		self._ws_outbox = []
		# The websocket session (background_task) joins the room and drives all
		# in-game communication from here on; nothing else is sent over HTTP.

	def _start_round(self):
		self._queue_ws_action("start")

	def _dismiss_score(self):
		# If the websocket is down there's no writer to drain the outbox and the
		# action would be discarded on teardown, leaving the user stuck on the
		# score screen. Fall back to advancing the local UI, mirroring the old
		# offline behaviour; a reconnect will resync from the server.
		if not self._ws_alive:
			self.session.set_room_state("waiting")
			return
		self._queue_ws_action("dismiss")

	def _queue_ws_action(self, action):
		# Button handlers run on the same asyncio loop as background_task, so a
		# plain list is a safe outbox — the ws loop drains it on its next tick.
		self._ws_outbox.append({"action": action})

	def _start_testing(self):
		modules = self.module_registry.connected_modules()
		if not modules:
			self.notification = Notification("No modules connected")
			return
		if self.menu:
			self.menu._cleanup()
			self.menu = None
		items = [m.friendly_name() for m in modules] + ["Back"]
		self.menu = Menu(
			self,
			items,
			select_handler=self._test_menu_select,
			back_handler=self._back_to_main,
		)

	def _test_menu_select(self, item, idx):
		if item == "Back":
			self._back_to_main()
			return
		module = self.module_registry.connected_modules()[idx]
		if self.menu:
			self.menu._cleanup()
			self.menu = None
		self._test_session = TestSession([module])

	def _show_qr_screen(self):
		if self.menu:
			self.menu._cleanup()
			self.menu = None
		self._qr_active = True

	def _exit_qr_screen(self):
		if not self._qr_active:
			return
		self._qr_active = False
		self._ensure_menu()

	def _get_qr_matrix(self):
		if self._qr_matrix is None:
			try:
				from .lib.uQR import QRCode
				qr = QRCode()
				qr.add_data("{}/register/{}".format(self.room_client.server_url, self._secret_id))
				self._qr_matrix = qr.get_matrix()
			except Exception:
				self._qr_matrix = False
		return self._qr_matrix if self._qr_matrix else None

	def _capabilities(self):
		return self.module_registry.get_capabilities()

	def _set_leds(self, colour):
		rgb = BADGE_COLOURS.get(colour, (0, 0, 0))
		for i in range(1, 13):
			tildagonos.leds[i] = rgb
		tildagonos.leds.write()

	def _leave_room(self):
		# Leaving simply tears down the websocket: stop_room() flips in_game
		# off, the ws loop exits and closes the socket, and the server drops the
		# badge from the room in its disconnect handler.
		self._set_leds(None)
		self.session.badge_colour = None

	async def background_task(self):
		print("[RC] background_task starting")
		while True:
			if self.session.in_game:
				print("[RC] ws: starting session for room {}".format(self.session.room_id))
				await self._run_ws_session()
				if self.session.in_game:
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
		ws_url = self.room_client.ws_url(self.session.room_id, self.badge_id)
		print("[RC] ws → {}".format(ws_url))
		ws = None
		reader_task = None
		try:
			ws = await self.room_client.connect_ws(ws_url)
			self._ws_alive = True
			self._ws_joined = False
			print("[RC] ws connected")

			# Join over the websocket: the server replies with our session
			# token, colour and the initial room state.
			caps = self._capabilities()
			await self._ws_send_json(ws, {"action": "join", "capabilities": caps})
			self._caps_sync.mark_sent(caps)

			reader_task = asyncio.create_task(self._ws_read_loop(ws))
			while self.session.in_game and self._ws_alive:
				await self._flush_ws_outbox(ws)
				await asyncio.sleep(0.1)
		except Exception as exc:
			print("[RC] ws error: {}".format(exc))
		finally:
			self._ws_alive = False
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
			self._ws_outbox = []
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
			self._ws_alive = False

	async def _ws_send_json(self, ws, obj):
		# Single choke point for outbound frames so every message is logged.
		print("[RC] ws SEND {}".format(json.dumps(obj)))
		await ws.send_json(obj)

	async def _flush_ws_outbox(self, ws):
		# Queued button actions (start/dismiss), then a capability update if
		# dirty, then a pending result. The result is captured-and-cleared
		# before awaiting the send so a result set by update() during the await
		# is preserved for the next flush rather than dropped or double-sent.
		while self._ws_outbox:
			msg = self._ws_outbox.pop(0)
			if self.session.session_token:
				msg["session_token"] = self.session.session_token
			await self._ws_send_json(ws, msg)
		caps = self._caps_sync.maybe_caps(self._capabilities())
		if caps is not None:
			self._caps_sync.mark_sent(caps)
			await self._ws_send_json(ws, {"capabilities": caps})
		result = self.session.pending_result
		if result:
			self.session.pending_result = None
			await self._ws_send_json(ws, {
				"result": result,
				"session_token": self.session.session_token,
			})

	def _apply_ws_state(self, data):
		if "error" in data:
			print("[RC] ws ← server error: {}".format(data["error"]))
			self.notification = Notification(data["error"])
			# An error before we've joined this session is a fatal join failure
			# (e.g. "Room is full" on a reconnect after being pruned): bail back
			# to the menu rather than reconnect-looping on the same rejection. A
			# stale session_token from a previous session is not evidence we're
			# in the room, so we key off this session's join, not the token.
			if not self._ws_joined:
				self.session.stop_room()
				self._ensure_menu()
			return
		self._ws_joined = True
		if data.get("need_capabilities"):
			self._caps_sync.mark_dirty()
		new_colour = self.session.apply_poll_response(
			data,
			now_ms=time.ticks_ms(),
			module_lookup=self._module_by_name,
		)
		if new_colour:
			self._set_leds(new_colour)

	def update(self, delta):
		if self._test_session is not None:
			self._test_session.update()
			if self._test_session.state == "done":
				self._test_session = None
		elif self.session.in_game:
			if self.session.cancel_hold_start is not None:
				held = time.ticks_diff(time.ticks_ms(), self.session.cancel_hold_start)
				if held >= CANCEL_HOLD_MS:
					self._leave_room()
					self.session.stop_room()
					self._ensure_menu()
					return

			if self.session.in_round and self.session.expected_module:
				status = self.session.expected_module.check_command()
				if status in (CommandStatus.PASSED, CommandStatus.FAILED):
					self.session.pending_result = self.session.build_result(status)

		elif not self._qr_active:
			self._ensure_menu()
			if self.menu:
				self.menu.update(delta)

		if self.notification:
			self.notification.update(delta)

	def draw(self, ctx):
		ctx.save()
		clear_background(ctx)
		ctx.text_align = ctx.CENTER
		ctx.text_baseline = ctx.MIDDLE

		if self._test_session is not None:
			if self._test_session.state == "command":
				self._draw_testing_command(ctx)
			elif self._test_session.state == "waiting":
				self._draw_testing_waiting(ctx)
			elif self._test_session.state == "summary":
				self._draw_testing_summary(ctx)
		elif self.session.room_state == "waiting":
			self._draw_waiting(ctx)
		elif self.session.room_state == "in-round":
			self._draw_in_round(ctx)
		elif self.session.room_state == "finished":
			self._draw_finished(ctx)
		elif self._qr_active:
			self._draw_qr_screen(ctx)
		elif self.menu:
			self.menu.draw(ctx)

		if self.notification:
			self.notification.draw(ctx)
		ctx.restore()

	def _instruction_fraction(self):
		s = self.session
		if s.display_time_remaining_s is None or s.display_timeout_s is None or s.display_updated_ms is None:
			return None
		elapsed_s = time.ticks_diff(time.ticks_ms(), s.display_updated_ms) / 1000
		remaining = s.display_time_remaining_s - elapsed_s
		return max(0.0, min(1.0, remaining / s.display_timeout_s))

	def _draw_waiting(self, ctx):
		ctx.rgb(0, 1, 0)
		ctx.font_size = 16
		ctx.move_to(0, -68).text("Room {}".format(self.session.room_id))

		players = self.session.players
		if players:
			y = -52
			for player in players:
				colour = player.get("colour", "")
				name = player.get("username") or colour
				rgb = BADGE_COLOURS.get(colour, (20, 20, 20))
				ctx.rgb(rgb[0] / 40, rgb[1] / 40, rgb[2] / 40)
				ctx.font_size = 11
				ctx.move_to(0, y).text(name)
				y += 12
		else:
			ctx.rgb(0, 1, 0)
			ctx.font_size = 14
			ctx.move_to(0, -40).text("{} badge{} connected".format(
				self.session.badge_count,
				"s" if self.session.badge_count != 1 else "",
			))

		ctx.rgb(0, 1, 0)
		ctx.font_size = 16
		if self.session.ready_count > 0:
			ctx.move_to(0, 30).text("{} / {} ready".format(self.session.ready_count, self.session.badge_count))
			ctx.font_size = 12
			if self.session.is_ready:
				ctx.rgb(0, 0.6, 0)
				ctx.move_to(0, 48).text("you're ready!")
			else:
				ctx.rgb(0.8, 0.8, 0)
				ctx.move_to(0, 48).text("press any button")
		else:
			ctx.move_to(0, 30).text("Press any button")
			ctx.move_to(0, 48).text("to start round")
		ctx.font_size = 10
		ctx.rgb(0.5, 0.5, 0.5)
		ctx.move_to(0, 68).text("hold cancel to leave")

	def _draw_in_round(self, ctx):
		ctx.rgb(0.4, 0.4, 0.4)
		ctx.font_size = 10
		ctx.move_to(0, -68).text("Room {}  Badge {}".format(self.session.room_id, self.badge_id[-6:]))
		ctx.rgb(0, 1, 0)
		if self.session.display_target_colour:
			ctx.font_size = 14
			ctx.move_to(0, -50).text(self.session.display_target_colour)
		ctx.font_size = 24
		ctx.move_to(0, -30).text(self.session.display_module_name or "")
		ctx.move_to(0, -4).text(self.session.display_command or "...")
		frac = self._instruction_fraction()
		if frac is not None:
			ctx.rgb(0.2, 0.2, 0.2)
			ctx.rectangle(-100, 10, 200, 5).fill()
			if frac > 0.5:
				ctx.rgb(0, 0.8, 0)
			elif frac > 0.25:
				ctx.rgb(0.8, 0.6, 0)
			else:
				ctx.rgb(0.8, 0.1, 0)
			ctx.rectangle(-100, 10, 200 * frac, 5).fill()
		ctx.rgb(0, 1, 0)
		ctx.font_size = 30
		ctx.move_to(0, 52).text(self.session.format_remaining(time.ticks_ms()))
		ctx.font_size = 10
		ctx.rgb(0.5, 0.5, 0.5)
		modules = ", ".join(m.friendly_name() for m in self.module_registry.connected_modules())
		ctx.move_to(0, 72).text(modules or "No modules")

	def _draw_testing_command(self, ctx):
		ts = self._test_session
		ctx.rgb(0, 1, 0)
		ctx.font_size = 12
		ctx.move_to(0, -68).text("Testing {}/{}".format(ts.index + 1, ts.total))
		ctx.font_size = 24
		ctx.move_to(0, -30).text(ts.current_module.friendly_name())
		ctx.move_to(0, -4).text(ts.current_command)
		ctx.font_size = 10
		ctx.rgb(0.5, 0.5, 0.5)
		ctx.move_to(0, 68).text("hold cancel to skip")

	def _draw_testing_waiting(self, ctx):
		ts = self._test_session
		ctx.rgb(0, 1, 0)
		ctx.font_size = 18
		ctx.move_to(0, -34).text("Waiting for")
		ctx.move_to(0, -12).text("commands...")
		ctx.font_size = 11
		ctx.rgb(0.5, 0.8, 0.5)
		ctx.move_to(0, 16).text("{} passed  {} skipped".format(ts.passed, ts.skipped))
		ctx.font_size = 10
		ctx.rgb(0.5, 0.5, 0.5)
		ctx.move_to(0, 56).text("interact with hexpansion")
		ctx.move_to(0, 70).text("hold cancel to finish")

	def _draw_testing_summary(self, ctx):
		ts = self._test_session
		ctx.rgb(0, 1, 0)
		ctx.font_size = 20
		ctx.move_to(0, -40).text("Test complete!")
		ctx.font_size = 16
		ctx.move_to(0, -10).text("{} passed".format(ts.passed))
		ctx.move_to(0, 15).text("{} skipped".format(ts.skipped))
		ctx.font_size = 10
		ctx.rgb(0.5, 0.5, 0.5)
		ctx.move_to(0, 55).text("press any button")

	def _draw_finished(self, ctx):
		scores = self.session.server_scores
		overall = self.session.overall_score

		ctx.rgb(0.4, 0.8, 0.4)
		ctx.font_size = 13
		ctx.move_to(0, -75).text("Round over!")

		ctx.rgb(0, 1, 0)
		if overall is not None:
			ctx.font_size = 11
			ctx.move_to(0, -52).text("Your team's score")
			ctx.font_size = 38
			ctx.move_to(0, -26).text("{}".format(overall))
			ctx.font_size = 14
			ctx.move_to(0, -3).text("{} pass  {} fail".format(
				scores.get("passed", 0), scores.get("failed", 0),
			))
		else:
			ctx.font_size = 22
			ctx.move_to(0, -40).text("{} pass".format(scores.get("passed", 0)))
			ctx.move_to(0, -16).text("{} fail".format(scores.get("failed", 0)))

		badge_scores = self.session.badge_scores
		if badge_scores:
			colour_names = {p["colour"]: p.get("username") or p["colour"] for p in self.session.players}
			ctx.font_size = 11
			ctx.rgb(0.5, 0.8, 0.5)
			y = 10
			for colour in sorted(badge_scores):
				s = badge_scores[colour]
				marker = "*" if colour == self.session.badge_colour else " "
				name = colour_names.get(colour, colour)
				ctx.move_to(0, y).text("{}{}: {} / {}".format(
					marker, name, s.get("passed", 0), s.get("failed", 0),
				))
				y += 13

		ctx.font_size = 14
		if self.session.dismissed_count > 0:
			ctx.rgb(0, 1, 0)
			ctx.move_to(0, 68).text("{} / {} ready".format(self.session.dismissed_count, self.session.badge_count))
			ctx.font_size = 10
			if self.session.is_dismissed:
				ctx.rgb(0, 0.6, 0)
				ctx.move_to(0, 82).text("you're ready")
			else:
				ctx.rgb(0.8, 0.8, 0)
				ctx.move_to(0, 82).text("press any button")
		else:
			ctx.font_size = 10
			ctx.rgb(0.4, 0.4, 0.4)
			ctx.move_to(0, 74).text("press any button to continue")

	def _draw_qr_screen(self, ctx):
		matrix = self._get_qr_matrix()
		if matrix:
			qr_size = len(matrix)
			pixel_size = max(3, int(160 / qr_size))
			total = pixel_size * qr_size
			ox = -(total // 2)
			oy = -(total // 2) - 10
			ctx.rgb(1, 1, 1).rectangle(ox - 4, oy - 4, total + 8, total + 8).fill()
			for row in range(qr_size):
				for col in range(qr_size):
					if matrix[row][col]:
						ctx.rgb(0, 0, 0).rectangle(
							ox + col * pixel_size,
							oy + row * pixel_size,
							pixel_size, pixel_size,
						).fill()
		else:
			ctx.rgb(0, 1, 0)
			ctx.font_size = 11
			ctx.move_to(0, -20).text("Scan to set name")
			ctx.font_size = 9
			url = self.room_client.server_url
			ctx.move_to(0, 0).text(url + "/register/")
			ctx.move_to(0, 14).text(self._secret_id)
		ctx.rgb(0.5, 0.5, 0.5)
		ctx.font_size = 9
		ctx.move_to(0, 80).text("any key to go back")


# Entry point discovered by the Tildagon app loader when publishing.
__app_export__ = RaceConditionApp
