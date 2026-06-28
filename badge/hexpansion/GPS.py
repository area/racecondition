import math
import time

from system.hexpansion.util import get_app_by_vid_pid

from .base import HexpansionModule, CommandStatus


TARGET_DISTANCE_M = 10


def _distance_m(lat1, lon1, lat2, lon2):
    # Flat-earth approximation, accurate enough for small distances
    dlat = (lat2 - lat1) * 111111
    dlon = (lon2 - lon1) * 111111 * math.cos(math.radians(lat1))
    return math.sqrt(dlat * dlat + dlon * dlon)


class GPSModule(HexpansionModule):
    # The GPS hexpansion runs its own firmware app (0x7CAB/0xBEAC) that owns
    # the UART and parses NMEA itself, exposing a .position (lat, lon) API.
    # We read fixes from that running app rather than driving the UART here.
    VID, PID = 0x7CAB, 0xBEAC
    COMMAND_OPTIONS = ["Move 10m away"]

    def reset(self):
        super().reset()
        self._start_pos = None
        self._command_started_ms = None
        self._gps = None

    def _current_pos(self):
        # The hexpansion app is created on insertion; if it isn't running yet
        # (or the hexpansion was removed) there's no fix to read.
        if self._gps is None:
            self._gps = get_app_by_vid_pid(self.VID, self.PID)
        if self._gps is None:
            return None
        return self._gps.position  # (lat, lon) tuple, or None while waiting for a fix

    def get_capabilities(self):
        pos = self._current_pos()
        commands = list(self.COMMAND_OPTIONS) if pos is not None else []
        return {"module": self.friendly_name(), "commands": commands}

    def set_command(self, command):
        result = super().set_command(command)
        self._setup_command()
        return result

    def _setup_command(self):
        self._command_started_ms = time.ticks_ms()
        self._start_pos = self._current_pos()  # snapshot current position (may be None)
        print("[GPS] Command setup. Start pos: {}".format(self._start_pos))

    def check_command(self):
        current = self._current_pos()

        # If we didn't have a fix when the command was issued, latch it now
        if self._start_pos is None:
            if current is not None:
                print("[GPS] Latching start pos: {}".format(current))
                self._start_pos = current
            return CommandStatus.WAITING

        if current is None:
            return CommandStatus.WAITING

        dist = _distance_m(
            self._start_pos[0], self._start_pos[1],
            current[0], current[1],
        )
        print("[GPS] Distance from start: {:.2f}m".format(dist))
        if dist >= TARGET_DISTANCE_M:
            print("[GPS] PASSED - moved {:.2f}m".format(dist))
            return CommandStatus.PASSED
        return CommandStatus.WAITING
