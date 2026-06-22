import app
import time

from machine import I2C

from app_components import Menu, Notification
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
from .constants import BADGE_COLOURS, CANCEL_HOLD_MS
from .identity import build_secret_id, derive_public_id
from .render import Renderer
from .network import NetworkController


class RaceConditionApp(app.App):
	def __init__(self, room_client=None):
		self.button_states = Buttons(self)
		self._secret_id = build_secret_id()
		self.badge_id = derive_public_id(self._secret_id)
		self.session = GameSession()
		self.module_registry = ModuleRegistry()
		self.notification = None
		self.room_client = room_client if room_client is not None else RoomClient()
		self.menu = None
		self._room_list = []
		self._cancel_down_event = None
		self._test_session = None
		self._qr_active = False
		self.net = NetworkController(self)
		self.renderer = Renderer(self)
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
		self.net.mark_caps_dirty()
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
		self.net.mark_caps_dirty()
		self.net.reset_outbox()
		# The websocket session (NetworkController.run) joins the room and drives
		# all in-game communication from here on; nothing else is sent over HTTP.

	def _start_round(self):
		self.net.queue_action("start")

	def _dismiss_score(self):
		# If the websocket is down there's no writer to drain the outbox and the
		# action would be discarded on teardown, leaving the user stuck on the
		# score screen. Fall back to advancing the local UI, mirroring the old
		# offline behaviour; a reconnect will resync from the server.
		if not self.net.alive:
			self.session.set_room_state("waiting")
			return
		self.net.queue_action("dismiss")

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
		await self.net.run()

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
		self.renderer.draw(ctx)


# Entry point discovered by the Tildagon app loader when publishing.
__app_export__ = RaceConditionApp
