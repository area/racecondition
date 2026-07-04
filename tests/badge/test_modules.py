import unittest
from unittest.mock import MagicMock, patch

from badge.hexpansion.base import CommandStatus
from badge.hexpansion import decorate_command
from badge.hexpansion.MegaDrive import (
    MegaDriveModule,
    DIAGONAL_COMMANDS,
    DIRECTIONS,
    COMBOS,
    COMBO_STEP_MS,
)
from badge.hexpansion.GPS import GPSModule, _distance_m, TARGET_DISTANCE_M
from badge.hexpansion.Keyboard import KeyboardModule, WORDS
from badge.hexpansion.Tildagon2024 import Tildagon2024Module
from badge.hexpansion.Tildagon2026 import Tildagon2026Module


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

def _tts(name):
    return _BtnEvent(name, "TwentyTwentySix")


def _perform_combo(m, tokens, held=None):
    # Drive a module through a combo's step tokens, transitioning the held D-pad
    # so each step lands as an exact input snapshot: release directions the next
    # step doesn't want, press the ones it does, then press any face button. The
    # final event of each transition produces the snapshot the matcher checks.
    held = set() if held is None else held
    for token in tokens:
        if token in DIRECTIONS:
            target_dirs = {token}
        elif token in DIAGONAL_COMMANDS:
            target_dirs = set(DIAGONAL_COMMANDS[token])
        else:
            target_dirs = held & DIRECTIONS  # button step keeps current directions
        for direction in list(held & DIRECTIONS):
            if direction not in target_dirs:
                held.discard(direction)
                m.on_button_up(_sega(direction))
        for direction in target_dirs:
            if direction not in held:
                held.add(direction)
                m.on_button_down(_sega(direction))
        if token not in DIRECTIONS and token not in DIAGONAL_COMMANDS:
            held.add(token)
            m.on_button_down(_sega(token))


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
            if cmd in COMBOS:
                _perform_combo(m, COMBOS[cmd])
            else:
                for direction in DIAGONAL_COMMANDS.get(cmd, (cmd,)):
                    m.on_button_down(_sega(direction))
            self.assertEqual(m.check_command(), CommandStatus.PASSED, msg=cmd)

    def test_unsupported_command_raises(self):
        with self.assertRaises(ValueError):
            self.m.set_command("turbo")

    def test_diagonal_passes_when_both_directions_held(self):
        self.m.set_command("up_left")
        self.m.on_button_down(_sega("up"))
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)
        self.m.on_button_down(_sega("left"))
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_diagonal_stays_waiting_with_only_one_direction(self):
        self.m.set_command("down_right")
        self.m.on_button_down(_sega("down"))
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)

    def test_diagonal_stays_waiting_if_first_released_before_second(self):
        # Releasing up before pressing left is a roll, not a corner press.
        self.m.set_command("up_left")
        self.m.on_button_down(_sega("up"))
        self.m.on_button_up(_sega("up"))
        self.m.on_button_down(_sega("left"))
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)

    def test_cardinal_latches_as_soon_as_its_direction_is_pressed(self):
        # Command is "up"; pressing up latches PASSED immediately, even if a
        # corner press then also holds left.
        self.m.set_command("up")
        self.m.on_button_down(_sega("up"))
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)
        self.m.on_button_down(_sega("left"))
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_set_command_clears_stale_held_buttons(self):
        # A direction held from a previous command must not satisfy a new diagonal.
        self.m.set_command("up")
        self.m.on_button_down(_sega("up"))
        self.m.set_command("up_left")
        self.m.on_button_down(_sega("left"))
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)


# ── MegaDrive combos ──────────────────────────────────────────────────────────

class TestMegaDriveCombos(unittest.TestCase):
    COMBO = "qcf_a"  # down -> down_right -> right -> A

    def setUp(self):
        self.m = MegaDriveModule()
        self.m.set_command(self.COMBO)

    def test_combo_is_advertised_in_capabilities(self):
        self.assertIn(self.COMBO, self.m.get_capabilities()["commands"])

    def test_combo_decorates_as_glyph_sequence(self):
        # The motion shows as its glyph sequence with no press verb.
        self.assertEqual(decorate_command("MegaDrive", self.COMBO), "↓ ↘ → A")

    def test_full_sequence_passes(self):
        self.m.on_button_down(_sega("down"))         # ↓
        self.m.on_button_down(_sega("right"))        # ↘ (down+right)
        self.m.on_button_up(_sega("down"))           # → (right only)
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)
        self.m.on_button_down(_sega("a"))            # + A
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_partial_sequence_stays_waiting(self):
        self.m.on_button_down(_sega("down"))
        self.m.on_button_down(_sega("right"))
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)

    def test_pressing_the_button_early_does_not_pass(self):
        # 'a' before the motion is finished must not complete the combo.
        self.m.on_button_down(_sega("a"))
        self.m.on_button_up(_sega("a"))
        self.m.on_button_down(_sega("down"))
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)

    def test_wrong_and_extra_inputs_are_ignored(self):
        # Advance-only: mashing other buttons mid-motion never resets progress.
        self.m.on_button_down(_sega("down"))
        self.m.on_button_down(_sega("b"))            # noise
        self.m.on_button_up(_sega("b"))
        self.m.on_button_down(_sega("right"))        # down+right
        self.m.on_button_up(_sega("down"))           # right
        self.m.on_button_down(_sega("a"))
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_overshoot_can_be_retried_within_the_window(self):
        # Rolling straight from down to right (skipping the corner) doesn't
        # advance; the player can re-roll to hit down_right and carry on.
        self.m.on_button_down(_sega("down"))         # ↓  -> step 1
        self.m.on_button_up(_sega("down"))
        self.m.on_button_down(_sega("right"))        # → only: step 2 wants ↘, no advance
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)
        self.m.on_button_down(_sega("down"))         # ↘ (down+right) -> step 2
        self.m.on_button_up(_sega("down"))           # → -> step 3
        self.m.on_button_down(_sega("a"))            # + A -> pass
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_slow_step_resets_the_combo(self):
        clock = {"t": 0}
        with patch("badge.hexpansion.MegaDrive.time.ticks_ms", side_effect=lambda: clock["t"]):
            self.m.set_command(self.COMBO)           # latches start time at t=0
            self.m.on_button_down(_sega("down"))     # step 1 at t=0
            clock["t"] = COMBO_STEP_MS + 1           # dawdle past the window
            self.m.on_button_down(_sega("right"))    # too slow -> reset to start
            self.assertEqual(self.m._combo_progress, 0)
            self.assertEqual(self.m.check_command(), CommandStatus.WAITING)


# ── GPS pure functions ────────────────────────────────────────────────────────
# NMEA parsing now lives in the GPS hexpansion's own firmware app, which exposes
# a parsed (lat, lon) .position; the badge no longer parses sentences itself.

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

class _FakeGPSApp:
    """Stand-in for the GPS hexpansion's running firmware app.

    Exposes a parsed ``.position`` of ``(lat, lon)``, or ``None`` while it is
    still waiting for a fix — exactly the surface GPSModule reads.
    """
    def __init__(self, position=None):
        self.position = position


class TestGPSCommandStateMachine(unittest.TestCase):
    CMD = "Move 10m away"

    def _make_module(self, position=None):
        m = GPSModule()
        # Inject the running hexpansion app directly; with _gps set, GPSModule
        # skips the get_app_by_vid_pid lookup and reads .position from this.
        m._gps = _FakeGPSApp(position)
        return m

    def test_waiting_when_no_fix(self):
        m = self._make_module()  # no fix yet
        m.set_command(self.CMD)
        self.assertEqual(m.check_command(), CommandStatus.WAITING)

    def test_latches_start_pos_when_fix_arrives_after_command(self):
        m = self._make_module()  # no fix at set_command time
        m.set_command(self.CMD)
        self.assertIsNone(m._start_pos)
        m._gps.position = (51.5, -0.1)  # fix arrives later
        result = m.check_command()
        self.assertEqual(result, CommandStatus.WAITING)
        self.assertEqual(m._start_pos, (51.5, -0.1))  # latched

    def test_passes_when_moved_far_enough(self):
        m = self._make_module((51.5, -0.1))  # start_pos snapshot at set_command
        m.set_command(self.CMD)
        m._gps.position = (51.5 + TARGET_DISTANCE_M / 111111 + 0.0001, -0.1)
        self.assertEqual(m.check_command(), CommandStatus.PASSED)

    def test_waiting_when_not_moved_enough(self):
        m = self._make_module((51.5, -0.1))
        m.set_command(self.CMD)
        self.assertEqual(m.check_command(), CommandStatus.WAITING)

    def test_waiting_when_fix_lost_after_start(self):
        # If the fix drops out mid-command we hold at WAITING rather than crash.
        m = self._make_module((51.5, -0.1))
        m.set_command(self.CMD)
        m._gps.position = None  # lost fix
        self.assertEqual(m.check_command(), CommandStatus.WAITING)

    def test_stays_waiting_when_not_moved_enough_over_time(self):
        m = self._make_module((51.5, -0.1))
        m.set_command(self.CMD)
        m.check_command()
        # No client-side timeout — stays WAITING indefinitely until server expires it
        self.assertEqual(m.check_command(), CommandStatus.WAITING)


class TestGPSCapabilities(unittest.TestCase):
    CMD = "Move 10m away"

    def test_command_lookup_is_lazy_and_cached(self):
        m = GPSModule()
        fake = _FakeGPSApp((51.5, -0.1))
        with patch("badge.hexpansion.GPS.get_app_by_vid_pid", return_value=fake) as lookup:
            self.assertEqual(m._current_pos(), (51.5, -0.1))
            lookup.assert_called_once_with(GPSModule.VID, GPSModule.PID)
            m._current_pos()  # cached — no second lookup
            lookup.assert_called_once()

    def test_no_command_offered_without_fix(self):
        m = GPSModule()
        m._gps = _FakeGPSApp(None)
        self.assertEqual(m.get_capabilities()["commands"], [])

    def test_command_offered_once_fixed(self):
        m = GPSModule()
        m._gps = _FakeGPSApp((51.5, -0.1))
        self.assertIn(self.CMD, m.get_capabilities()["commands"])


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


# ── Keyboard (keepdeck) ───────────────────────────────────────────────────────

def _key(name):
    return _BtnEvent(name, "Keyboard")


class TestKeyboardModule(unittest.TestCase):
    def setUp(self):
        self.m = KeyboardModule()
        self.m.set_command("pause")

    def _type(self, text):
        for ch in text:
            self.m.on_button_down(_key("SPACE" if ch == " " else ch.upper()))

    def test_typing_each_word_passes(self):
        for word in WORDS:
            m = KeyboardModule()
            m.set_command(word)
            for ch in word:
                m.on_button_down(_key(ch.upper()))
            self.assertEqual(m.check_command(), CommandStatus.PASSED, msg=word)

    def test_wrong_homophone_stays_waiting(self):
        self._type("paws")
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)

    def test_partial_word_stays_waiting(self):
        self._type("paus")
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)

    def test_backspace_fixes_a_typo(self):
        self._type("pauze")
        self.m.on_button_down(_key("BACKSPACE"))
        self.m.on_button_down(_key("BACKSPACE"))
        self._type("se")
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_earlier_junk_is_forgiven(self):
        # A failed attempt followed by the full word still passes.
        self._type("pawspause")
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_junk_mid_word_breaks_the_match(self):
        self._type("pau!se")
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)

    def test_space_breaks_the_match(self):
        self._type("pau se")
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)

    def test_modifier_keys_do_not_type(self):
        self._type("paus")
        for name in ("SHIFT", "ENTER", "UP", "ESCAPE"):
            self.m.on_button_down(_key(name))
        self._type("e")
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_keys_from_other_groups_ignored(self):
        for ch in "pause":
            self.m.on_button_down(_ttt(ch.upper()))
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)

    def test_set_command_clears_typed_buffer(self):
        self._type("pause")
        self.m.set_command("paws")
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)
        self._type("paws")
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_buffer_stays_bounded(self):
        self._type("x" * 200)
        self.assertLessEqual(len(self.m._typed), 32)
        self._type("pause")
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_no_word_contains_another(self):
        # The ends-with matcher relies on this property of the word list.
        for a in WORDS:
            for b in WORDS:
                if a != b:
                    self.assertNotIn(a, b)

    def test_capabilities_lists_all_words(self):
        self.assertEqual(self.m.get_capabilities()["commands"], list(WORDS))

    def test_decorate_quotes_the_word(self):
        phrase = decorate_command("keepdeck", "pours")
        self.assertIn('"pours"', phrase)


# ── Tildagon2026 ──────────────────────────────────────────────────────────────

class TestTildagon2026Module(unittest.TestCase):
    def setUp(self):
        import imu as _imu
        _imu.acc_read.return_value = (0.0, 0.0, 9.8)
        self.m = Tildagon2026Module()

    def test_connected_on_2026_pids(self):
        # conftest reports a 2024 board, so the 2026 module is disconnected...
        self.assertFalse(self.m.is_connected({}))
        # ...and connected on either Spaceagon revision.
        for pid in (0x2600, 0x2601):
            with patch("badge.hexpansion.Tildagon2024.detect_frontboard", return_value=pid):
                self.assertTrue(self.m.is_connected({}), msg=hex(pid))

    def test_2024_module_not_connected_on_2026_board(self):
        with patch("badge.hexpansion.Tildagon2024.detect_frontboard", return_value=0x2600):
            self.assertFalse(Tildagon2024Module().is_connected({}))

    def test_face_button_passes(self):
        self.m.set_command("a")
        self.m.on_button_down(_tts("A"))
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_button_from_2024_group_ignored(self):
        self.m.set_command("a")
        self.m.on_button_down(_ttt("A"))
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)

    def test_joystick_direction_passes(self):
        self.m.set_command("joy_up")
        self.m.on_button_down(_tts("JOYUP"))
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_joystick_wrong_direction_stays_waiting(self):
        self.m.set_command("joy_up")
        self.m.on_button_down(_tts("JOYDOWN"))
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)

    def test_fire_maps_to_joystick_button(self):
        self.m.set_command("fire")
        self.m.on_button_down(_tts("JOYFIRE"))
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_wave_maps_to_prox_sensor(self):
        self.m.set_command("wave_left")
        self.m.on_button_down(_tts("RIGHTPROX"))
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)
        self.m.on_button_down(_tts("LEFTPROX"))
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_any_touch_pad_satisfies_touch(self):
        for pad in ("TOUCH1", "TOUCH7", "TOUCH12"):
            m = Tildagon2026Module()
            m.set_command("touch")
            m.on_button_down(_tts(pad))
            self.assertEqual(m.check_command(), CommandStatus.PASSED, msg=pad)

    def test_touch_pad_does_not_satisfy_other_commands(self):
        self.m.set_command("a")
        self.m.on_button_down(_tts("TOUCH1"))
        self.assertEqual(self.m.check_command(), CommandStatus.WAITING)

    def test_inherited_shake_passes_on_large_delta(self):
        import imu as _imu
        self.m.set_command("shake")
        self.m.check_command()  # stores _last_accel
        _imu.acc_read.return_value = (20.0, 20.0, 20.0)
        self.assertEqual(self.m.check_command(), CommandStatus.PASSED)

    def test_capabilities_excludes_gestures_when_hexpansions_present(self):
        with patch("badge.hexpansion.Tildagon2024.detect_frontboard", return_value=0x2600):
            self.m.is_connected({1: {"known": True, "name": "GPS"}})
        commands = self.m.get_capabilities()["commands"]
        self.assertNotIn("shake", commands)
        self.assertNotIn("flip", commands)
        self.assertIn("joy_up", commands)

    def test_decorate_joystick_shows_arrow(self):
        self.assertTrue(decorate_command("Tildagon 2026", "joy_up").endswith("↑"))

    def test_decorate_fire(self):
        self.assertTrue(decorate_command("Tildagon 2026", "fire").endswith("FIRE"))

    def test_decorate_face_button_falls_back_to_2024_arrows(self):
        self.assertTrue(decorate_command("Tildagon 2026", "b").endswith("↗"))

    def test_decorate_touch_is_a_phrase(self):
        self.assertIn(decorate_command("Tildagon 2026", "touch"),
                      ("Touch a pad", "Tap any touch pad"))


if __name__ == "__main__":
    unittest.main()
