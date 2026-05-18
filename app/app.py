import app
import time

from machine import I2C

from app_components import Menu, clear_background, Notification
from events.input import Buttons, BUTTON_TYPES
from system.eventbus import eventbus
from system.hexpansion.events import \
    HexpansionRemovalEvent, HexpansionInsertionEvent
from system.hexpansion.config import HexpansionConfig

from system.hexpansion.util import read_hexpansion_header, detect_eeprom_addr

try:
    from .hexpansion_names import get_friendly_name
except ImportError:
    from hexpansion_names import get_friendly_name


MAIN_MENU_ITEMS = ["Start Game", "Quit"]


class ExampleApp(app.App):
    def __init__(self):
        self.button_states = Buttons(self)
        self.menu = None
        self.show_clock = False
        self.clock_started_at = time.time()
        self.hexpansions = {}
        self.text = "No hexpansion found."
        self.color = (1, 0, 0)
        self.notification = None
        self._set_menu()
        self.scan_for_hexpansion()

        eventbus.on(
            HexpansionInsertionEvent,
            self.handle_hexpansion_insertion,
            self)
        eventbus.on(
            HexpansionRemovalEvent,
            self.handle_hexpansion_removal,
            self)

    def handle_hexpansion_insertion(self, event):
        print("Hexpansion inserted event received.")
        print(event)
        self.notification = Notification(
            "Hexpansion plugged in on port {}".format(event.port),
            port=event.port,
        )
        self.scan_for_hexpansion()

    def handle_hexpansion_removal(self, event):
        print("Hexpansion removed event received.")
        print(event)
        self.scan_for_hexpansion()

    def _cleanup(self):
        if self.menu:
            self.menu._cleanup()
            self.menu = None
        eventbus.remove(
            HexpansionInsertionEvent,
            self.handle_hexpansion_insertion,
            self)
        eventbus.remove(
            HexpansionRemovalEvent,
            self.handle_hexpansion_removal,
            self)

    def _set_menu(self):
        if self.menu:
            self.menu._cleanup()
        self.menu = Menu(
            self,
            MAIN_MENU_ITEMS,
            select_handler=self.select_handler,
            back_handler=self.back_handler,
        )

    def select_handler(self, item, idx):
        if item == "Start Game":
            self.show_clock = True
            self.clock_started_at = time.time()
        elif item == "Quit":
            self._cleanup()
            self.button_states.clear()
            self.minimise()

    def back_handler(self):
        self._cleanup()
        self.button_states.clear()
        self.minimise()

    def _format_clock(self):
        elapsed = int(time.time() - self.clock_started_at)
        minutes = elapsed // 60
        seconds = elapsed % 60
        return "{:02d}:{:02d}".format(minutes, seconds)

    def _draw_status_text(self, ctx):
        lines = self.text.split("\n")
        start_y = -80
        line_spacing = 18

        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE
        ctx.font_size = 16

        for index, line in enumerate(lines[:8]):
            ctx.move_to(0, start_y + (index * line_spacing)).text(line)

    def _refresh_status_text(self):
        if not self.hexpansions:
            self.color = (1, 0, 0)
            self.text = "No hexpansion found."
            return

        self.color = (0, 1, 0)
        lines = ["Connected hexpansions:"]

        for port in sorted(self.hexpansions):
            item = self.hexpansions[port]
            if item["known"]:
                lines.append("p{}: {}".format(port, item["name"]))
            else:
                lines.append(
                    "p{}: unknown {}:{}".format(
                        port,
                        item["vid_hex"],
                        item["pid_hex"],
                    )
                )

        self.text = "\n".join(lines)

    def update(self, delta):
        if self.button_states.get(BUTTON_TYPES["CANCEL"]):
            if self.show_clock:
                self.show_clock = False
                self.button_states.clear()
            else:
                self._cleanup()
                self.button_states.clear()
                self.minimise()

        if self.menu and not self.show_clock:
            self.menu.update(delta)

        if self.notification:
            self.notification.update(delta)

    def draw(self, ctx):
        ctx.save()
        clear_background(ctx)
        if self.show_clock:
            x, y, z = self.color
            ctx.rgb(x, y, z)
            self._draw_status_text(ctx)
            ctx.font_size = 36
            ctx.move_to(0, 55).text(self._format_clock())
        elif self.menu:
            self.menu.draw(ctx)
        if self.notification:
            self.notification.draw(ctx)
        ctx.restore()

    def scan_for_hexpansion(self):
        connected = {}

        for port in range(1, 7):
            print(f"Searching for hexpansion on port: {port}")
            i2c = I2C(port)
            print("Scanning I2C bus...")
            addr, addr_len = detect_eeprom_addr(i2c)
            # print("Found EEPROM at addr " + hex(addr))

            if addr is None:
                # Is the detect pin high?
                # print("No EEPROM found at port " + str(port)
                #       + ". Is the detect pin high?")
                # hexpansionConfig = HexpansionConfig(port)
                # print("Hexpansion config: " + str(hexpansionConfig.__dict__))
                continue
            else:
                print("Found EEPROM at addr " + hex(addr))

            header = read_hexpansion_header(i2c, addr, addr_len=addr_len)
            if header is None:
                connected[port] = {
                    "name": "Unknown (no header)",
                    "known": False,
                    "vid_hex": "n/a",
                    "pid_hex": "n/a",
                }
                continue
            else:
                print("Read header: " + str(header))

            friendly_name = get_friendly_name(header.vid, header.pid)
            vid_hex = hex(header.vid)
            pid_hex = hex(header.pid)
            known = friendly_name is not None

            connected[port] = {
                "name": friendly_name or "Unknown",
                "known": known,
                "vid_hex": vid_hex,
                "pid_hex": pid_hex,
            }

            # # Swap 0xCAFE with your EEPROM header vid
            # # Swap 0xCAFF with your EEPROM header pid
            # if (header.vid == 0xCAFE) and (header.pid == 0xCAFF):
            #     print("Found the desired hexpansion in port " + str(port))
            #     self.color = (0, 1, 0)
            # else:
            #     print()
            hexpansionConfig = HexpansionConfig(port)
            print("Hexpansion config: " + str(hexpansionConfig))

        self.hexpansions = connected
        self._refresh_status_text()

        return None
