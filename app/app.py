import app
import random
import time

from machine import I2C

from app_components import Menu, clear_background, Notification
from events.input import Buttons, ButtonDownEvent, ButtonUpEvent
from system.eventbus import eventbus
from system.hexpansion.events import HexpansionRemovalEvent, HexpansionInsertionEvent
from system.hexpansion.util import read_hexpansion_header, detect_eeprom_addr

from .hexpansion_names import get_friendly_name
from .hexpansion import get_connected_modules, CommandStatus


CANCEL_HOLD_MS = 4000


class ExampleApp(app.App):
    def __init__(self):
        self.button_states = Buttons(self)
        self.in_game = False
        self.cancel_hold_start = None
        self.connected_modules = []
        self.active_module = None
        self.current_command = None
        self.score_pass = 0
        self.score_fail = 0
        self.game_start_time = None
        self.notification = None
        self.menu = Menu(
            self,
            ["Start Game", "Quit"],
            select_handler=self._menu_select,
            back_handler=self._menu_back,
        )
        self._scan()
        eventbus.on(HexpansionInsertionEvent, self._on_insert, self)
        eventbus.on(HexpansionRemovalEvent, self._on_remove, self)
        eventbus.on(ButtonDownEvent, self._on_button_down, self)
        eventbus.on(ButtonUpEvent, self._on_button_up, self)

    def _cleanup(self):
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
        if self.in_game and self.active_module:
            self.active_module.on_button_down(event)

    def _on_button_up(self, event):
        if self._is_cancel(event):
            self.cancel_hold_start = None

    def _menu_select(self, item, idx):
        if item == "Start Game":
            self.score_pass = 0
            self.score_fail = 0
            self.game_start_time = time.time()
            self.in_game = True
            self._next_command()
        elif item == "Quit":
            self.in_game = False
            self.button_states.clear()
            self.minimise()

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

    def _next_command(self):
        if self.connected_modules:
            self.active_module = random.choice(self.connected_modules)
            self.current_command = self.active_module.generate_command()
        else:
            self.active_module = None
            self.current_command = None

    def update(self, delta):
        if self.in_game:
            if self.cancel_hold_start is not None:
                held = time.ticks_diff(time.ticks_ms(), self.cancel_hold_start)
                if held >= CANCEL_HOLD_MS:
                    self.in_game = False
                    self.cancel_hold_start = None
                    return

            if self.active_module:
                status = self.active_module.check_command()
                if status == CommandStatus.PASSED:
                    self.score_pass += 1
                    self._next_command()
                elif status == CommandStatus.FAILED:
                    self.score_fail += 1
                    self._next_command()
        else:
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
            ctx.font_size = 24
            ctx.move_to(0, -40).text(self.current_command or "No module connected")
            ctx.font_size = 18
            ctx.move_to(0, 0).text(
                "Pass: {}  Fail: {}".format(self.score_pass, self.score_fail)
            )
            ctx.font_size = 36
            ctx.move_to(0, 40).text(self._format_clock())
            ctx.font_size = 14
            modules = ", ".join(m.FRIENDLY_NAME for m in self.connected_modules)
            ctx.move_to(0, 75).text(modules or "No modules")
        elif self.menu:
            self.menu.draw(ctx)
        if self.notification:
            self.notification.draw(ctx)
        ctx.restore()
