import app
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

BADGE_COLOURS = {
    "red":    (40,  0,  0),
    "green":  ( 0, 40,  0),
    "blue":   ( 0,  0, 40),
    "yellow": (30, 30,  0),
    "purple": (25,  0, 25),
    "orange": (40, 15,  0),
}

CANCEL_HOLD_MS = 4000
ROOM_COUNT = 5
SERVER_POLL_INTERVAL_MS = 750


def _build_badge_id():
	try:
		import machine
		import ubinascii

		return ubinascii.hexlify(machine.unique_id()).decode("utf-8")
	except Exception:
		return "badge-{}".format(time.ticks_ms())


class TildateamApp(app.App):
	def __init__(self, room_client=None):
		self.button_states = Buttons(self)
		self.badge_id = _build_badge_id()
		self.session = GameSession()
		self.connected_modules = []
		self.module_registry = ModuleRegistry()
		self._network_error_shown = False
		self.notification = None
		self.room_client = room_client if room_client is not None else RoomClient()
		self.menu = None
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
		if self._is_cancel(event):
			if self.session.in_game and self.session.cancel_hold_start is None:
				self.session.cancel_hold_start = time.ticks_ms()
			return

		if self.session.room_state == "waiting":
			self._start_round()
		elif self.session.room_state == "finished":
			self._dismiss_score()
		elif self.session.in_round and self.session.expected_module:
			self.session.expected_module.on_button_down(event)

	def _on_button_up(self, event):
		if self._is_cancel(event):
			self.session.cancel_hold_start = None

	def _menu_items(self):
		items = []
		for room in range(1, ROOM_COUNT + 1):
			items.append("Join Room {}".format(room))
		items.append("Quit")
		return items

	def _menu_select(self, item, idx):
		if item.startswith("Join Room "):
			room = int(item.split(" ")[-1])
			self._start_room(room)
		elif item == "Quit":
			self._leave_room()
			self.session.stop_room()
			self.button_states.clear()
			self.minimise()

	def _ensure_menu(self):
		if not self.menu:
			self.menu = Menu(
				self,
				self._menu_items(),
				select_handler=self._menu_select,
				back_handler=self._menu_back,
			)

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
		self.connected_modules = self.module_registry.scan(hexpansions)
		if self.session.expected_module and self.module_registry.get_by_name(self.session.expected_module.FRIENDLY_NAME) is None:
			self.session.clear_assignment()

	def _module_by_name(self, module_name):
		return self.module_registry.get_by_name(module_name)

	def _start_room(self, room_id):
		if self.menu:
			self.menu._cleanup()
			self.menu = None
		self._network_error_shown = False
		self.session.start_room(room_id)
		self.module_registry.reset_connected()
		if self.room_client.available():
			_, error = self.room_client.join_room(
				self.session.room_id,
				self.badge_id,
				self._capabilities(),
			)
			if error:
				self.notification = Notification("Join failed: {}".format(error))
		self._poll_server(force=True)

	def _start_round(self):
		if not self.room_client.available():
			return
		_, error = self.room_client.start_round(self.session.room_id, self.badge_id)
		if error:
			self.notification = Notification("Start failed: {}".format(error))
		else:
			self._poll_server(force=True)

	def _dismiss_score(self):
		if not self.room_client.available():
			self.session.set_room_state("waiting")
			return
		_, error = self.room_client.dismiss_score(self.session.room_id, self.badge_id)
		if error:
			self.notification = Notification("Dismiss failed: {}".format(error))
		else:
			self._poll_server(force=True)

	def _set_assignment(self, assignment):
		if not assignment:
			self.session.clear_assignment()
			return

		module_name = assignment.get("module")
		command = assignment.get("command")
		assignment_id = assignment.get("id")

		module = self._module_by_name(module_name)
		if not module:
			self.session.clear_assignment()
			return

		if self.session.expected_command_id != assignment_id:
			try:
				module.set_command(command)
			except Exception:
				self.session.clear_assignment()
				return

		self.session.set_assignment(module, assignment_id, command)

	def _set_display(self, display):
		self.session.set_display(display)

	def _capabilities(self):
		return self.module_registry.get_capabilities()

	def _set_leds(self, colour):
		rgb = BADGE_COLOURS.get(colour, (0, 0, 0))
		for i in range(1, 13):
			tildagonos.leds[i] = rgb
		tildagonos.leds.write()

	def _leave_room(self):
		self._set_leds(None)
		self.session.badge_colour = None
		if self.room_client.available():
			_, error = self.room_client.leave_room(
				self.session.room_id,
				self.badge_id,
			)
			if error:
				print("[App] Leave failed: {}".format(error))

	def _poll_server(self, force=False):
		if not self.session.in_game:
			return

		now_ms = time.ticks_ms()
		if not force and self.session.last_poll_ms is not None:
			if time.ticks_diff(now_ms, self.session.last_poll_ms) < SERVER_POLL_INTERVAL_MS:
				return
		self.session.last_poll_ms = now_ms

		if not self.room_client.available():
			if not self._network_error_shown:
				err = self.room_client._import_error or "urequests not found"
				self.notification = Notification("No network: {}".format(err))
				self._network_error_shown = True
			return

		data, error = self.room_client.poll(
			self.session.room_id,
			self.badge_id,
			self._capabilities(),
			result=self.session.pending_result,
		)
		if error:
			self.notification = Notification("Server error: {}".format(error))
			return
		if data is None:
			return

		self.session.pending_result = None
		self.session.set_room_state(data.get("room_state", self.session.room_state))
		self.session.badge_count = data.get("badge_count", 0)
		self.session.time_remaining_s = data.get("time_remaining_s")
		self.session.server_scores = data.get("scores", self.session.server_scores)

		if self.session.in_round:
			self._set_assignment(data.get("assignment"))
			self._set_display(data.get("display"))

		colour = data.get("colour")
		if colour and colour != self.session.badge_colour:
			self.session.badge_colour = colour
			self._set_leds(colour)

	def update(self, delta):
		if self.session.in_game:
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

			self._poll_server(force=False)
		else:
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

		if self.session.room_state == "waiting":
			self._draw_waiting(ctx)
		elif self.session.room_state == "in-round":
			self._draw_in_round(ctx)
		elif self.session.room_state == "finished":
			self._draw_finished(ctx)
		elif self.menu:
			self.menu.draw(ctx)

		if self.notification:
			self.notification.draw(ctx)
		ctx.restore()

	def _draw_waiting(self, ctx):
		ctx.rgb(0, 1, 0)
		ctx.font_size = 20
		ctx.move_to(0, -40).text("Room {}".format(self.session.room_id))
		ctx.font_size = 14
		ctx.move_to(0, -10).text("{} badge{} connected".format(
			self.session.badge_count,
			"s" if self.session.badge_count != 1 else "",
		))
		ctx.font_size = 16
		ctx.move_to(0, 20).text("Press any button")
		ctx.move_to(0, 40).text("to start round")
		ctx.font_size = 10
		ctx.rgb(0.5, 0.5, 0.5)
		ctx.move_to(0, 68).text("hold cancel to leave")

	def _draw_in_round(self, ctx):
		ctx.rgb(0, 1, 0)
		ctx.font_size = 14
		ctx.move_to(0, -58).text("Room {}  Badge {}".format(self.session.room_id, self.badge_id[-6:]))
		ctx.font_size = 16
		ctx.move_to(0, -38).text(self.session.display_module_name or "Waiting for room commands")
		ctx.font_size = 24
		ctx.move_to(0, -14).text(self.session.display_command or "...")
		ctx.font_size = 14
		if self.session.expected_command:
			ctx.move_to(0, 10).text("Task assigned - use your controls")
		else:
			ctx.move_to(0, 10).text("Waiting for assignment")
		ctx.font_size = 18
		ctx.move_to(0, 30).text("Pass: {}  Fail: {}".format(self.session.score_pass, self.session.score_fail))
		ctx.font_size = 30
		ctx.move_to(0, 58).text(self.session.format_remaining())
		ctx.font_size = 12
		modules = ", ".join(m.FRIENDLY_NAME for m in self.connected_modules)
		ctx.move_to(0, 77).text(modules or "No modules")

	def _draw_finished(self, ctx):
		scores = self.session.server_scores
		ctx.rgb(0, 1, 0)
		ctx.font_size = 22
		ctx.move_to(0, -45).text("Round over!")
		ctx.font_size = 18
		ctx.move_to(0, -15).text("Pass: {}".format(scores.get("passed", 0)))
		ctx.move_to(0, 15).text("Fail: {}".format(scores.get("failed", 0)))
		ctx.font_size = 14
		ctx.rgb(0.5, 0.5, 0.5)
		ctx.move_to(0, 50).text("Press any button to continue")
