import math
import time
from machine import UART, Pin

from .base import HexpansionModule, CommandStatus


TARGET_DISTANCE_M = 10


def _distance_m(lat1, lon1, lat2, lon2):
    # Flat-earth approximation, accurate enough for small distances
    dlat = (lat2 - lat1) * 111111
    dlon = (lon2 - lon1) * 111111 * math.cos(math.radians(lat1))
    return math.sqrt(dlat * dlat + dlon * dlon)


def _parse_nmea_rmc(line):
    parts = line.split(",")
    if parts[0] not in ("$GNRMC", "$GPRMC"):
        return None
    if parts[2] != "A":  # A = valid fix
        return None
    lat_raw, lat_dir = parts[3], parts[4]
    lon_raw, lon_dir = parts[5], parts[6]
    if not lat_raw or not lon_raw:
        return None
    lat = float(lat_raw[:2]) + float(lat_raw[2:]) / 60
    lon = float(lon_raw[:3]) + float(lon_raw[3:]) / 60
    if lat_dir == "S":
        lat = -lat
    if lon_dir == "W":
        lon = -lon
    return {"lat": lat, "lon": lon}


class GPSModule(HexpansionModule):
    VID, PID = 0xCAFE, 0x1295
    COMMAND_OPTIONS = ["move 10m away"]

    def __init__(self):
        super().__init__()
        self._uart = UART(1, baudrate=9600, tx=Pin(34), rx=Pin(33))
        self._buffer = b""

    def reset(self):
        super().reset()
        self._current_pos = None
        self._start_pos = None
        self._command_started_ms = None
        self._buffer = b""

    def get_capabilities(self):
        self._read_uart()
        commands = list(self.COMMAND_OPTIONS) if self._current_pos is not None else []
        return {"module": self.friendly_name(), "commands": commands}

    def set_command(self, command):
        result = super().set_command(command)
        self._setup_command()
        return result

    def _setup_command(self):
        self._command_started_ms = time.ticks_ms()
        self._start_pos = self._current_pos  # snapshot current position (may be None)
        print("[GPS] Command setup. Start pos: {}".format(self._start_pos))

    def check_command(self):
        self._read_uart()

        # If we didn't have a fix when the command was issued, latch it now
        if self._start_pos is None:
            if self._current_pos is not None:
                print("[GPS] Latching start pos: {}".format(self._current_pos))
                self._start_pos = self._current_pos
            return CommandStatus.WAITING

        dist = _distance_m(
            self._start_pos["lat"], self._start_pos["lon"],
            self._current_pos["lat"], self._current_pos["lon"],
        )
        print("[GPS] Distance from start: {:.2f}m".format(dist))
        if dist >= TARGET_DISTANCE_M:
            print("[GPS] PASSED - moved {:.2f}m".format(dist))
            return CommandStatus.PASSED
        return CommandStatus.WAITING

    def _read_uart(self):
        if self._uart.any():
            data = self._uart.read(self._uart.any())
            print("[GPS] UART raw: {}".format(data))
            self._buffer += data
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                decoded_line = line.decode().strip()
                if decoded_line:
                    print("[GPS] UART line: {}".format(decoded_line))
                result = _parse_nmea_rmc(decoded_line)
                if result:
                    print("[GPS] Fix: lat={:.6f}, lon={:.6f}".format(result["lat"], result["lon"]))
                    self._current_pos = result
            except Exception as e:
                print("[GPS] Failed to parse line: {}".format(line))
                print("[GPS] Exception:", e)
                pass
