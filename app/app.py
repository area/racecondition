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
from .hexpansion import get_connected_modules, get_capabilities, CommandStatus
from .room_client import RoomClient

BADGE_COLOURS = {
    "red": (40, 0, 0),
    "green": (0, 40, 0),
    "blue": (0, 0, 40),
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


class ExampleApp(app.App):
	def __init__(self):
		self.button_states = Buttons(self)
		self.badge_id = _build_badge_id()
		self.room_id = 1
		self.in_game = False
		self.cancel_hold_start = None
		self.connected_modules = []
		self.expected_module = None
		self.expected_command_id = None
		self.expected_command = None
		self.display_module_name = None
		self.display_command = None
		self.pending_result = None
		self.last_poll_ms = None
		self._network_error_shown = False
		self.score_pass = 0
		self.score_fail = 0
		self.game_start_time = None
		self.notification = None
		self.badge_colour = None
		self.room_client = RoomClient()
		self.menu = None
		self._scan()
		self._ensure_menu()
		eventbus.emit(PatternDisable())

		eventbus.on(HexpansionInsertionEvent, self._on_insert, self)
		eventbus.on(HexpansionRemovalEvent, self._on_remove, self)
		eventbus.on(ButtonDownEvent, self._on_button_down, self)
		eventbus.on(ButtonUpEvent, self._on_button_up, self)

	def _cleanup(self):
		if self.in_game:
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
		if self._is_cancel(event) and self.in_game:
			if self.cancel_hold_start is None:
				self.cancel_hold_start = time.ticks_ms()
		if self.in_game and self.expected_module:
			self.expected_module.on_button_down(event)

	def _on_button_up(self, event):
		if self._is_cancel(event):
			self.cancel_hold_start = None

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
			self.in_game = False
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
		self.connected_modules = get_connected_modules(hexpansions)

	def _module_by_name(self, module_name):
		for module in self.connected_modules:
			if module.FRIENDLY_NAME == module_name:
				return module
		return None

	def _start_room(self, room_id):
		if self.menu:
			self.menu._cleanup()
			self.menu = None
		self.room_id = room_id
		self._network_error_shown = False
		self.score_pass = 0
		self.score_fail = 0
		self.expected_module = None
		self.expected_command_id = None
		self.expected_command = None
		self.display_module_name = None
		self.display_command = None
		self.pending_result = None
		self.last_poll_ms = None
		self.game_start_time = time.time()
		self.in_game = True
		self.cancel_hold_start = None
		if self.room_client.available():
			_, error = self.room_client.join_room(
				self.room_id,
				self.badge_id,
				self._capabilities(),
			)
			if error:
				self.notification = Notification("Join failed: {}".format(error))
		self._poll_server(force=True)

	def _set_assignment(self, assignment):
		if not assignment:
			self.expected_module = None
			self.expected_command_id = None
			self.expected_command = None
			return

		module_name = assignment.get("module")
		command = assignment.get("command")
		assignment_id = assignment.get("id")

		module = self._module_by_name(module_name)
		if not module:
			self.expected_module = None
			self.expected_command_id = None
			self.expected_command = None
			return

		if self.expected_command_id != assignment_id:
			try:
				module.set_command(command)
			except Exception:
				self.expected_module = None
				self.expected_command_id = None
				self.expected_command = None
				return

		self.expected_module = module
		self.expected_command_id = assignment_id
		self.expected_command = command

	def _set_display(self, display):
		if not display:
			self.display_module_name = None
			self.display_command = None
			return
		self.display_module_name = display.get("module")
		command = display.get("command")
		colour = display.get("target_colour")
		if colour:
			self.display_command = "{}: {}".format(colour[0].upper() + colour[1:], command)
		else:
			self.display_command = command

	def _capabilities(self):
		return get_capabilities(self.connected_modules)

	def _set_leds(self, colour):
		rgb = BADGE_COLOURS.get(colour, (0, 0, 0))
		for i in range(1, 13):
			tildagonos.leds[i] = rgb
		tildagonos.leds.write()

	def _leave_room(self):
		self._set_leds(None)
		self.badge_colour = None
		if self.room_client.available():
			_, error = self.room_client.leave_room(
				self.room_id,
				self.badge_id,
			)
			if error:
				print("[App] Leave failed: {}".format(error))

	def _poll_server(self, force=False):
		if not self.in_game:
			return

		now_ms = time.ticks_ms()
		if not force and self.last_poll_ms is not None:
			if time.ticks_diff(now_ms, self.last_poll_ms) < SERVER_POLL_INTERVAL_MS:
				return
		self.last_poll_ms = now_ms

		if not self.room_client.available():
			if not self._network_error_shown:
				err = self.room_client._import_error or "urequests not found"
				self.notification = Notification("No network: {}".format(err))
				self._network_error_shown = True
			return

		data, error = self.room_client.poll(
			self.room_id,
			self.badge_id,
			self._capabilities(),
			result=self.pending_result,
		)
		if error:
			self.notification = Notification("Server error: {}".format(error))
			return

		self.pending_result = None
		self._set_assignment(data.get("assignment"))
		self._set_display(data.get("display"))
		colour = data.get("colour")
		if colour and colour != self.badge_colour:
			self.badge_colour = colour
			self._set_leds(colour)

	def update(self, delta):
		if self.in_game:
			if self.cancel_hold_start is not None:
				held = time.ticks_diff(time.ticks_ms(), self.cancel_hold_start)
				if held >= CANCEL_HOLD_MS:
					self._leave_room()
					self.in_game = False
					self.cancel_hold_start = None
					self._ensure_menu()
					return

			if self.expected_module:
				status = self.expected_module.check_command()
				if status == CommandStatus.PASSED:
					self.score_pass += 1
					self.pending_result = {
						"assignment_id": self.expected_command_id,
						"status": status,
						"module": self.expected_module.FRIENDLY_NAME,
						"command": self.expected_command,
					}
					self.expected_module = None
					self.expected_command_id = None
					self.expected_command = None
				elif status == CommandStatus.FAILED:
					self.score_fail += 1
					self.pending_result = {
						"assignment_id": self.expected_command_id,
						"status": status,
						"module": self.expected_module.FRIENDLY_NAME,
						"command": self.expected_command,
					}
					self.expected_module = None
					self.expected_command_id = None
					self.expected_command = None

			self._poll_server(force=False)
		else:
			self._ensure_menu()
			if self.menu:
				self.menu.update(delta)

		if self.notification:
			self.notification.update(delta)

	def _format_clock(self):
		elapsed = int(time.time() - self.game_start_time)
		return "{:02d}:{:02d}".format(elapsed // 60, elapsed % 60)

	def draw(self, ctx):
		ctx.save()
		clear_background(ctx)
		if self.in_game:
			ctx.text_align = ctx.CENTER
			ctx.text_baseline = ctx.MIDDLE
			ctx.rgb(0, 1, 0)
			ctx.font_size = 14
			ctx.move_to(0, -58).text("Room {}  Badge {}".format(self.room_id, self.badge_id[-6:]))
			ctx.font_size = 16
			ctx.move_to(0, -38).text(self.display_module_name or "Waiting for room commands")
			ctx.font_size = 24
			ctx.move_to(0, -14).text(self.display_command or "...")
			ctx.font_size = 14
			if self.expected_command:
				ctx.move_to(0, 10).text("Task assigned - use your controls")
			else:
				ctx.move_to(0, 10).text("Waiting for assignment")
			ctx.font_size = 18
			ctx.move_to(0, 30).text("Pass: {}  Fail: {}".format(self.score_pass, self.score_fail))
			ctx.font_size = 30
			ctx.move_to(0, 58).text(self._format_clock())
			ctx.font_size = 12
			modules = ", ".join(m.FRIENDLY_NAME for m in self.connected_modules)
			ctx.move_to(0, 77).text(modules or "No modules")
		elif self.menu:
			self.menu.draw(ctx)
		if self.notification:
			self.notification.draw(ctx)
		ctx.restore()
