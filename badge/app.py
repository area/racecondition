import app
import asyncio
import ota
import settings
import time

from machine import I2C

from app_components import Menu, Notification, clear_background
from events.input import Buttons, ButtonDownEvent, ButtonUpEvent
from system.eventbus import eventbus
from system.hexpansion.events import HexpansionRemovalEvent, HexpansionInsertionEvent
from system.hexpansion.util import read_hexpansion_header, detect_eeprom_addr
from system.patterndisplay.events import PatternDisable
from tildagonos import tildagonos

from .buttons import is_cancel
from .hexpansion_names import get_friendly_name
from .constants import BADGE_COLOURS, CANCEL_HOLD_MS
from .identity import build_secret_id, derive_public_id

# The launcher runs `__import__` + `RaceConditionApp()` synchronously inside
# its own event loop, so the screen stays frozen on the launcher menu until
# both finish — and MicroPython compiles every .py it imports on-device. Only
# firmware-frozen modules and our few tiny helpers are imported above; the
# bulk of the app (~1700 lines) is imported in _finish_init() on the first
# update tick after a frame has been drawn, so it compiles behind the loading
# screen instead of in front of a frozen launcher. These names are bound as
# module globals there:
#   CommandStatus, TestSession,
#   fill_frame, fill_up_frame, FLASH_GREEN, FLASH_RED


class RaceConditionApp(app.App):
	def __init__(self, room_client=None):
		self.button_states = Buttons(self)
		self.notification = None
		self.menu = None
		# draw() shows the loading screen until _finish_init() builds the
		# real renderer.
		self.renderer = None
		self._loading = True
		self._loading_frame_drawn = False
		self._injected_room_client = room_client
		# Race Condition relies on hexpansion app-discovery APIs added in
		# tildagonOS v2; on v1 we bail out early and the renderer shows a
		# "requires v2" screen instead of crashing deeper in setup.
		self._os_unsupported = ota.get_version().startswith("v1.")
		if self._os_unsupported:
			from .render import Renderer
			self.renderer = Renderer(self)
			# The only interaction on the unsupported screen is backing out:
			# any button drops us back to the launcher.
			eventbus.on(ButtonDownEvent, self._on_unsupported_button, self)
			return

	def _finish_init(self):
		global CommandStatus, TestSession, fill_frame, fill_up_frame, FLASH_GREEN, FLASH_RED
		from .hexpansion import ModuleRegistry, CommandStatus
		from .room_client import RoomClient
		from .session import GameSession
		from .test_session import TestSession
		from .leds import LedRing, fill_frame, fill_up_frame, FLASH_GREEN, FLASH_RED
		from .render import Renderer
		from .network import NetworkController

		self._secret_id = build_secret_id()
		self.badge_id = derive_public_id(self._secret_id)
		self.session = GameSession()
		self.module_registry = ModuleRegistry()
		self.room_client = self._injected_room_client if self._injected_room_client is not None else RoomClient()
		self._room_list = []
		self._cancel_down_event = None
		self._test_session = None
		self._qr_active = False
		self.net = NetworkController(self)
		self.leds = LedRing(self._write_leds)
		self._scan()
		self._ensure_menu()
		self.renderer = Renderer(self)
		eventbus.emit(PatternDisable())

		eventbus.on(HexpansionInsertionEvent, self._on_insert, self)
		eventbus.on(HexpansionRemovalEvent, self._on_remove, self)
		eventbus.on(ButtonDownEvent, self._on_button_down, self)
		eventbus.on(ButtonUpEvent, self._on_button_up, self)
		self._loading = False

	def _cleanup(self):
		if self._os_unsupported:
			eventbus.remove(ButtonDownEvent, self._on_unsupported_button, self)
			return
		if self._loading:
			# _finish_init hasn't run: nothing constructed, no handlers
			# registered, nothing to tear down.
			return
		if self.session.in_game:
			self._leave_room()
		self._close_menu()
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

	def _on_unsupported_button(self, event):
		self.button_states.clear()
		self.minimise()

	def _on_button_down(self, event):
		if self._test_session is not None:
			self._test_session.on_button_down(event)
			return

		if self._qr_active:
			self._exit_qr_screen()
			return

		if is_cancel(event):
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

		if is_cancel(event):
			if (self.session.cancel_hold_start is not None
					and self.session.in_round
					and self.session.expected_module is not None
					and self._cancel_down_event is not None):
				held = time.ticks_diff(time.ticks_ms(), self.session.cancel_hold_start)
				if held < CANCEL_HOLD_MS:
					self.session.expected_module.on_button_down(self._cancel_down_event)
			self.session.cancel_hold_start = None
			self._cancel_down_event = None
			return

		if self.session.in_round and self.session.expected_module:
			self.session.expected_module.on_button_up(event)

	def _main_menu_items(self):
		return ["Join Room", "Create Room", "Set name", "Test modules", "Quit"]

	def _close_menu(self):
		# Menu has no public teardown; _cleanup() is the firmware component's
		# own destructor, so this is the one sanctioned reach into it.
		if self.menu:
			self.menu._cleanup()
			self.menu = None

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
		self._close_menu()
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
		self._close_menu()

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
		self.net.mark_caps_dirty()
		if self.session.expected_module and self.module_registry.get_by_name(self.session.expected_module.friendly_name()) is None:
			self.session.clear_assignment()

	def _module_by_name(self, module_name):
		return self.module_registry.get_by_name(module_name)

	def _start_room(self, room_id):
		self._close_menu()
		self.session.start_room(room_id)
		self.module_registry.reset_connected()
		self.net.mark_caps_dirty()
		self.net.reset_outbox()
		# Show the "Connecting..." placeholder from the instant we enter the room,
		# not just once _run_ws_session starts; otherwise a stale joined=True from
		# the previous session would flash the old room's status for a tick.
		self.net.joined = False
		# The websocket session (NetworkController.run) joins the room and drives
		# all in-game communication from here on; nothing else is sent over HTTP.

	def _start_round(self):
		# Any button toggles readiness. The local state is updated
		# optimistically (flag, ready count, own lobby dot) so the screen
		# reacts on the press and a quick second press sends the opposite
		# action; the server's next push overwrites it with the truth.
		if self.session.is_ready:
			self.net.queue_action("unready")
			self.session.set_local_ready(False)
		else:
			self.net.queue_action("start")
			self.session.set_local_ready(True)

	def _dismiss_score(self):
		# If the websocket is down there's no writer to drain the outbox and the
		# action would be discarded on teardown, leaving the user stuck on the
		# score screen. Fall back to advancing the local UI, mirroring the old
		# offline behaviour; a reconnect will resync from the server.
		if not self.net.alive:
			self.session.set_room_state("waiting")
			return
		# Same toggle as the waiting room: a ready ("dismissed") badge can
		# back out until everyone is ready.
		if self.session.is_dismissed:
			self.net.queue_action("undismiss")
			self.session.set_local_dismissed(False)
		else:
			self.net.queue_action("dismiss")
			self.session.set_local_dismissed(True)

	def _start_testing(self):
		modules = self.module_registry.connected_modules()
		if not modules:
			self.notification = Notification("No modules connected")
			return
		self._close_menu()
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
		self._close_menu()
		self._test_session = TestSession([module])

	def _show_qr_screen(self):
		self._close_menu()
		self._qr_active = True

	def _exit_qr_screen(self):
		if not self._qr_active:
			return
		self._qr_active = False
		self._ensure_menu()

	def _set_leds(self, colour):
		self.leds.set_base(BADGE_COLOURS.get(colour, (0, 0, 0)))

	def _write_leds(self, frame):
		# Our palette is full-range; honour the user's "Pattern brightness"
		# setting herea
		b = settings.get("pattern_brightness", 0.1)
		for i, (r, g, bl) in enumerate(frame):
			tildagonos.leds[i + 1] = (int(r * b), int(g * b), int(bl * b))
		tildagonos.leds.write()

	def _leave_room(self):
		# Leaving simply tears down the websocket: stop_room() flips in_game
		# off, the ws loop exits and closes the socket, and the server drops the
		# badge from the room in its disconnect handler.
		self._set_leds(None)
		self.session.badge_colour = None

	async def background_task(self):
		if self._os_unsupported:
			return
		while self._loading:
			await asyncio.sleep(0.05)
		await self.net.run()

	def update(self, delta):
		if self._os_unsupported:
			return
		if self._loading:
			# Give the render task one pass first so the loading frame is on
			# screen before _finish_init blocks the loop on module compilation
			# and the I2C scan.
			if self._loading_frame_drawn:
				self._finish_init()
			return
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

			if self.session.in_round:
				if self.session.expected_module:
					status = self.session.expected_module.check_command()
					if status in (CommandStatus.PASSED, CommandStatus.FAILED):
						self.session.pending_result = self.session.build_result(status)
						if status == CommandStatus.PASSED:
							self.leds.flash(FLASH_GREEN, time.ticks_ms(), fill_up_frame)
						else:
							self.leds.flash(FLASH_RED, time.ticks_ms(), fill_frame)
						self.renderer.flash_result(status == CommandStatus.PASSED, time.ticks_ms())
				if self.session.assignment_timed_out:
					self.session.assignment_timed_out = False
					self.leds.flash(FLASH_RED, time.ticks_ms(), fill_frame)
					self.renderer.flash_result(False, time.ticks_ms())

		elif not self._qr_active:
			self._ensure_menu()
			if self.menu:
				self.menu.update(delta)

		self.leds.update(time.ticks_ms())

		if self.notification:
			self.notification.update(delta)

	def draw(self, ctx):
		if self.renderer is None:
			self._draw_loading(ctx)
			return
		self.renderer.draw(ctx)

	def _draw_loading(self, ctx):
		ctx.save()
		clear_background(ctx)
		ctx.text_align = ctx.CENTER
		ctx.text_baseline = ctx.MIDDLE
		ctx.rgb(0, 1, 0)
		ctx.font_size = 24
		ctx.move_to(0, -10).text("Race Condition")
		ctx.font_size = 12
		ctx.rgb(0.5, 0.5, 0.5)
		ctx.move_to(0, 20).text("loading...")
		ctx.restore()
		self._loading_frame_drawn = True


# Entry point discovered by the Tildagon app loader when publishing.
__app_export__ = RaceConditionApp
