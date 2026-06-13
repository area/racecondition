import unittest
from unittest.mock import MagicMock, patch

from app.hexpansion.base import CommandStatus, HexpansionModule
from app.hexpansion.MegaDrive import MegaDriveModule
from app.hexpansion.GPS import GPSModule, _parse_nmea_rmc, _distance_m, TARGET_DISTANCE_M
from app.hexpansion.Tildagon2024 import Tildagon2024Module


# ── Fake button event helpers ─────────────────────────────────────────────────

class _Btn:
    def __init__(self, name, group):
        self.name = name
        self.group = group

class _BtnEvent:
    def __init__(self, name, group):
        self.button = _Btn(name, group)


def _sega(name):
    return _BtnEvent(name, "SegaController")

def _ttt(name):
    return _BtnEvent(name, "TwentyTwentyFour")


# ── MegaDrive ─────────────────────────────────────────────────────────────────

class TestMegaDriveModule(unittest.TestCase):
    def setUp(self):
        self.m = MegaDriveModule()
        self.m.set_command("a")

    def test_initial_state_is_waiting(self):
        m = MegaDriveModule()
        m.set_command("start")
        self.assertEqual(m.check_command(), CommandStatus.WAITING)

    def test_correct_button_passes(self):
        self.m.on_button_down(_sega("a"))
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_wrong_button_stays_waiting(self):
        self.m.on_button_down(_sega("b"))
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)

    def test_button_from_other_group_is_ignored(self):
        self.m.on_button_down(_ttt("a"))
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)

    def test_all_command_options_are_recognised(self):
        for cmd in MegaDriveModule.COMMAND_OPTIONS:
            m = MegaDriveModule()
            m.set_command(cmd)
            m.on_button_down(_sega(cmd))
            self.assertEqual(m.check_command(), CommandStatus.PASSED, msg=cmd)

    def test_unsupported_command_raises(self):
        with self.assertRaises(ValueError):
            self.m.set_command("turbo")


# ── GPS pure functions ────────────────────────────────────────────────────────

class TestParseNmeaRmc(unittest.TestCase):
    VALID_GNRMC = "$GNRMC,123519,A,5130.0000,N,00007.0000,W,0.0,0.0,230394,,,A*6A"
    VALID_GPRMC = "$GPRMC,123519,A,5130.0000,N,00007.0000,W,0.0,0.0,230394,,,A*6A"

    def test_valid_gnrmc_returns_fix(self):
        result = _parse_nmea_rmc(self.VALID_GNRMC)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["lat"], 51.5, places=3)

    def test_valid_gprmc_accepted(self):
        self.assertIsNotNone(_parse_nmea_rmc(self.VALID_GPRMC))

    def test_invalid_status_returns_none(self):
        invalid = self.VALID_GNRMC.replace(",A,", ",V,")
        self.assertIsNone(_parse_nmea_rmc(invalid))

    def test_non_rmc_sentence_returns_none(self):
        self.assertIsNone(_parse_nmea_rmc("$GPGGA,123519,5130.0000,N,00007.0000,W,1,08,0.9,545.4,M,,,,*47"))

    def test_west_longitude_is_negative(self):
        result = _parse_nmea_rmc(self.VALID_GNRMC)
        self.assertLess(result["lon"], 0)

    def test_south_latitude_is_negative(self):
        south = "$GNRMC,123519,A,5130.0000,S,00007.0000,E,0.0,0.0,230394,,,A*6A"
        result = _parse_nmea_rmc(south)
        self.assertLess(result["lat"], 0)


class TestDistanceM(unittest.TestCase):
    def test_same_point_is_zero(self):
        self.assertAlmostEqual(_distance_m(51.5, -0.1, 51.5, -0.1), 0.0)

    def test_five_metres_north(self):
        # 5m north ≈ 0.000045 degrees latitude
        lat_offset = 5 / 111111
        d = _distance_m(51.5, -0.1, 51.5 + lat_offset, -0.1)
        self.assertAlmostEqual(d, 5.0, delta=0.1)

    def test_larger_distance(self):
        # ~111m north
        d = _distance_m(51.5, -0.1, 51.501, -0.1)
        self.assertGreater(d, 100)


# ── GPS command state machine ─────────────────────────────────────────────────

class TestGPSCommandStateMachine(unittest.TestCase):
    def _make_module(self):
        m = GPSModule()
        m._uart.any.return_value = 0  # silence the UART mock
        return m

    def test_waiting_when_no_fix(self):
        m = self._make_module()
        m.set_command("move 5m away")
        self.assertEqual(m.check_command(), CommandStatus.WAITING)

    def test_latches_start_pos_on_first_fix(self):
        m = self._make_module()
        m._current_pos = {"lat": 51.5, "lon": -0.1}
        m.set_command("move 5m away")
        # start_pos was None at set_command time; first check latches it
        result = m.check_command()
        self.assertEqual(result, CommandStatus.WAITING)
        self.assertIsNotNone(m._start_pos)

    def test_passes_when_moved_far_enough(self):
        m = self._make_module()
        m._current_pos = {"lat": 51.5, "lon": -0.1}
        m.set_command("move 5m away")
        m.check_command()  # latches start_pos
        m._current_pos = {"lat": 51.5 + TARGET_DISTANCE_M / 111111 + 0.0001, "lon": -0.1}
        self.assertEqual(m.check_command(), CommandStatus.PASSED)

    def test_waiting_when_not_moved_enough(self):
        m = self._make_module()
        m._current_pos = {"lat": 51.5, "lon": -0.1}
        m.set_command("move 5m away")
        m.check_command()  # latches start_pos
        self.assertEqual(m.check_command(), CommandStatus.WAITING)

    def test_stays_waiting_when_not_moved_enough_over_time(self):
        m = self._make_module()
        m._current_pos = {"lat": 51.5, "lon": -0.1}
        m.set_command("move 5m away")
        m.check_command()  # latches start_pos
        # No client-side timeout — stays WAITING indefinitely until server expires it
        self.assertEqual(m.check_command(), CommandStatus.WAITING)


# ── Tildagon2024 ──────────────────────────────────────────────────────────────

class TestTildagon2024Module(unittest.TestCase):
    def setUp(self):
        import imu as _imu
        _imu.acc_read.return_value = (0.0, 0.0, 9.8)
        self.m = Tildagon2024Module()

    def test_is_always_connected(self):
        self.assertTrue(self.m.is_connected({}))

    def test_capabilities_excludes_shake_when_hexpansions_present(self):
        self.m.is_connected({1: {"known": True, "name": "GPS"}})
        self.assertNotIn("shake", self.m.get_capabilities()["commands"])

    def test_capabilities_includes_shake_when_no_hexpansions(self):
        self.m.is_connected({})
        self.assertIn("shake", self.m.get_capabilities()["commands"])

    def test_correct_button_passes(self):
        self.m.set_command("a")
        self.m.on_button_down(_ttt("a"))
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_wrong_button_stays_waiting(self):
        self.m.set_command("a")
        self.m.on_button_down(_ttt("b"))
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)

    def test_button_from_other_group_ignored(self):
        self.m.set_command("a")
        self.m.on_button_down(_sega("a"))
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)

    def test_shake_passes_on_large_delta(self):
        import imu as _imu
        self.m.set_command("shake")
        self.m.check_command()  # stores _last_accel = (0, 0, 9.8)
        _imu.acc_read.return_value = (20.0, 20.0, 20.0)
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_shake_waiting_on_small_delta(self):
        import imu as _imu
        self.m.set_command("shake")
        self.m.check_command()  # stores _last_accel
        _imu.acc_read.return_value = (0.1, 0.0, 9.8)  # tiny movement
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)

    def test_shake_stays_waiting_without_movement(self):
        import imu as _imu
        self.m.set_command("shake")
        self.m.check_command()  # stores _last_accel
        _imu.acc_read.return_value = (0.0, 0.0, 9.8)  # no change
        # No client-side timeout — stays WAITING until server expires it
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)


if __name__ == "__main__":
    unittest.main()
