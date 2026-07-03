"""Tests for the in-round instruction ring and target-colour rendering."""

import math
import time

from badge.render import Renderer, RING_RADIUS, SPLASH_MAX_MS


class FakeCtx:
    """Records the drawing calls the renderer makes, tagged with the colour
    and line width in effect at the time."""

    CENTER = "center"
    MIDDLE = "middle"
    LEFT = "left"

    def __init__(self):
        self.arcs = []
        self.texts = []
        self.fills = []
        self.font_size = 12
        self.line_width = 1
        self.text_align = None
        self.text_baseline = None
        self._rgb = None
        self._pos = (0, 0)
        self._rect = None

    def save(self):
        return self

    def restore(self):
        return self

    def rgb(self, r, g, b):
        self._rgb = (r, g, b)
        return self

    def move_to(self, x, y):
        self._pos = (x, y)
        return self

    def line_to(self, x, y):
        return self

    def arc(self, x, y, radius, start, end, ccw):
        self._arc = {
            "radius": radius,
            "sweep": end - start,
            "rgb": self._rgb,
            "line_width": self.line_width,
        }
        self.arcs.append(self._arc)
        return self

    def stroke(self):
        self._arc = None
        return self

    def fill(self):
        if self._rect is not None:
            self.fills.append({"rect": self._rect, "rgb": self._rgb})
            self._rect = None
        elif getattr(self, "_arc", None) is not None:
            self.fills.append({"arc": self._arc, "rgb": self._rgb})
            self._arc = None
        return self

    def rectangle(self, x, y, w, h):
        self._rect = (x, y, w, h)
        return self

    def text(self, s):
        self.texts.append({
            "text": s,
            "rgb": self._rgb,
            "pos": self._pos,
            "font_size": self.font_size,
        })
        return self

    def text_width(self, s):
        return len(s) * 6


class FakeApp:
    def __init__(self, target_colour):
        class Session:
            display_target_colour = target_colour

        self.session = Session()


def make_renderer(target_colour):
    return Renderer(FakeApp(target_colour))


def test_colour_rgb_normalises_brightest_channel_to_full():
    r = make_renderer(None)
    assert r._colour_rgb("Blue") == (0.0, 0.0, 1.0)
    red, green, blue = r._colour_rgb("Orange")
    assert red == 1.0 and 0 < green < 1 and blue == 0.0


def test_colour_rgb_handles_capitalised_session_value():
    # session.set_display capitalises the colour ("blue" -> "Blue"); the
    # palette lookup must still resolve it.
    assert make_renderer(None)._colour_rgb("White") == (1.0, 1.0, 1.0)


def test_colour_rgb_falls_back_to_green():
    r = make_renderer(None)
    assert r._colour_rgb(None) == (0, 1, 0)
    assert r._colour_rgb("Mauve") == (0, 1, 0)


def test_ring_draws_single_traffic_light_sweep():
    # A single arc per frame (the dim trace was cut for draw-time reasons),
    # coloured by remaining thirds: green, then yellow, then red/blinking.
    ctx = FakeCtx()
    make_renderer("Blue")._draw_instruction_ring(ctx, 0.8)
    assert len(ctx.arcs) == 1
    sweep = ctx.arcs[0]
    assert sweep["radius"] == RING_RADIUS
    assert sweep["sweep"] == 0.8 * 2 * math.pi
    assert sweep["rgb"] == (0, 1, 0)

    ctx = FakeCtx()
    make_renderer("Blue")._draw_instruction_ring(ctx, 0.5)
    assert ctx.arcs[0]["rgb"] == (0.9, 0.9, 0)


def test_ring_expired_draws_nothing():
    ctx = FakeCtx()
    make_renderer("Blue")._draw_instruction_ring(ctx, 0.0)
    assert ctx.arcs == []


class FakeModuleRegistry:
    def connected_modules(self):
        return []


class FakeInRoundSession:
    def __init__(self, now_ms):
        self.room_id = 1
        self.display_target_colour = "Blue"
        self.display_module_name = "Flux Capacitor"
        self.display_instruction = "engage reversal"
        self.display_timeout_s = 8
        self.display_time_remaining_s = 6
        self.display_updated_ms = now_ms
        # The instruction arrived a moment ago; result splashes started
        # after this hold until it advances.
        self.display_changed_ms = now_ms - 500

    def remaining_seconds(self, now=None):
        return 60

    def format_remaining(self, now=None):
        return "01:00"


class FakeInRoundApp:
    def __init__(self, now_ms):
        self.session = FakeInRoundSession(now_ms)
        self.badge_id = "badge-a1b2c3"
        self.module_registry = FakeModuleRegistry()


def draw_in_round(session_setup=None, renderer_setup=None):
    ctx = FakeCtx()
    app = FakeInRoundApp(time.ticks_ms())
    if session_setup:
        session_setup(app.session)
    renderer = Renderer(app)
    if renderer_setup:
        renderer_setup(renderer)
    renderer._draw_in_round(ctx)
    return ctx


def find_text(ctx, s):
    return next(t for t in ctx.texts if t["text"] == s)


def test_in_round_banner_is_full_brightness_target_colour():
    from badge.render import BANNER_RADIUS

    ctx = draw_in_round()
    banner = ctx.fills[0]
    assert banner["arc"]["radius"] == BANNER_RADIUS
    assert banner["rgb"] == (0.0, 0.0, 1.0)


def test_banner_word_contrast_follows_luminance():
    # Blue is dark: white word. Yellow is bright: black word.
    ctx = draw_in_round()
    assert find_text(ctx, "Blue")["rgb"] == (1, 1, 1)

    def yellow(s):
        s.display_target_colour = "Yellow"

    ctx = draw_in_round(yellow)
    assert find_text(ctx, "Yellow")["rgb"] == (0, 0, 0)


def test_no_target_colour_means_no_banner():
    def clear_colour(s):
        s.display_target_colour = None

    ctx = draw_in_round(clear_colour)
    assert ctx.fills == []


def test_instruction_text_is_plain_green():
    # No white-hot pop, no jitter: the instruction renders steady.
    def just_changed(s):
        s.display_changed_ms = time.ticks_ms()

    ctx = draw_in_round(just_changed)
    t = find_text(ctx, "engage reversal")
    assert t["rgb"] == (0, 1, 0)
    assert t["font_size"] == 24
    assert t["pos"] == (0, -4)


def test_result_splash_holds_until_next_instruction():
    def splash_now(r):
        r.flash_result(True, time.ticks_ms())

    # No new instruction yet: the splash holds.
    ctx = draw_in_round(renderer_setup=splash_now)
    assert find_text(ctx, "NICE!")

    # A newer instruction dismisses it.
    def newer_instruction(s):
        s.display_changed_ms = time.ticks_ms() + 10

    ctx = draw_in_round(newer_instruction, splash_now)
    assert not any(t["text"] == "NICE!" for t in ctx.texts)


def test_result_splash_safety_cap():
    start = time.ticks_ms() - SPLASH_MAX_MS - 100

    def stale_instruction(s):
        s.display_changed_ms = start - 50

    def splash_old(r):
        r.flash_result(False, start)

    ctx = draw_in_round(stale_instruction, splash_old)
    assert not any(t["text"] == "MISS!" for t in ctx.texts)


def test_fail_splash_says_miss():
    def splash_now(r):
        r.flash_result(False, time.ticks_ms())

    ctx = draw_in_round(renderer_setup=splash_now)
    assert find_text(ctx, "MISS!")


class FakeNet:
    joined = True


class FakeWaitingSession:
    def __init__(self):
        self.room_id = 2
        self.badge_colour = "blue"
        self.players = [
            {"colour": "blue", "username": "alex", "ready": True},
            {"colour": "orange", "username": "orange", "ready": False},
        ]
        self.ready_count = 1
        self.is_ready = True
        self.badge_count = 2


class FakeWaitingApp:
    def __init__(self):
        self.session = FakeWaitingSession()
        self.net = FakeNet()


def draw_waiting(mutate=None):
    ctx = FakeCtx()
    app = FakeWaitingApp()
    if mutate:
        mutate(app)
    Renderer(app)._draw_waiting(ctx)
    return ctx


def test_waiting_banner_teaches_own_colour():
    from badge.render import WAIT_BANNER_RADIUS

    ctx = draw_waiting()
    banner = ctx.fills[0]
    assert banner["arc"]["radius"] == WAIT_BANNER_RADIUS
    assert banner["rgb"] == (0.0, 0.0, 1.0)
    assert find_text(ctx, "YOU ARE")
    assert find_text(ctx, "Blue")


def test_lobby_draws_one_slot_per_colour():
    from badge.render import LOBBY_DOT_R

    ctx = draw_waiting()
    slots = [a for a in ctx.arcs if a["radius"] == LOBBY_DOT_R]
    assert len(slots) == 6
    # Ready blue: filled dot. Unready orange: stroked in colour.
    dot_fills = [f for f in ctx.fills if "arc" in f and f["arc"]["radius"] == LOBBY_DOT_R]
    assert len(dot_fills) == 1
    assert dot_fills[0]["rgb"] == (0.0, 0.0, 1.0)
    empty = [a for a in slots if a["rgb"] == (0.18, 0.18, 0.18)]
    assert len(empty) == 4


def test_lobby_marks_own_slot_with_white_ring():
    from badge.render import LOBBY_DOT_R

    ctx = draw_waiting()
    rings = [a for a in ctx.arcs if a["radius"] == LOBBY_DOT_R + 4.5]
    assert len(rings) == 1
    assert rings[0]["rgb"] == (1, 1, 1)


class FakeFinishedSession:
    def __init__(self):
        self.server_scores = {"passed": 12, "failed": 3}
        self.overall_score = 154.37
        self.badge_scores = {
            "blue": {"passed": 8, "failed": 1},
            "orange": {"passed": 4, "failed": 2},
        }
        self.players = [
            {"colour": "blue", "username": "alex", "ready": False},
            {"colour": "orange", "username": "orange", "ready": False},
        ]
        self.badge_colour = "blue"
        self.badge_count = 2
        self.dismissed_count = 0
        self.is_dismissed = False
        self.rank = 3
        self.total_games = 27


class FakeFinishedApp:
    def __init__(self):
        self.session = FakeFinishedSession()


def draw_finished(elapsed_ms, mutate=None):
    ctx = FakeCtx()
    app = FakeFinishedApp()
    if mutate:
        mutate(app)
    renderer = Renderer(app)
    renderer._finished_ms = time.ticks_ms() - elapsed_ms
    renderer._draw_finished(ctx)
    return ctx


def test_finished_score_counts_up_then_lands_exact():
    from badge.render import COUNT_UP_MS

    ctx = draw_finished(COUNT_UP_MS // 2)
    assert find_text(ctx, str(int(154.37 // 2)))
    assert not any("games" in t["text"] for t in ctx.texts)

    ctx = draw_finished(2 * COUNT_UP_MS)
    assert find_text(ctx, "154.37")


def test_finished_rank_reveals_after_count_up():
    from badge.render import COUNT_UP_MS

    ctx = draw_finished(2 * COUNT_UP_MS)
    assert find_text(ctx, "#3 of 27 today")


def test_ring_blinks_red_when_time_is_low():
    # In the final third the arc is red and alternates between full and dim;
    # sample enough phase-distinct frames to see both states.
    import time as _time

    seen = set()
    for offset in range(0, 1000, 125):
        ctx = FakeCtx()
        renderer = make_renderer("Blue")
        real_ticks = _time.ticks_ms
        _time.ticks_ms = lambda base=real_ticks(), o=offset: base + o
        try:
            renderer._draw_instruction_ring(ctx, 0.1)
        finally:
            _time.ticks_ms = real_ticks
        seen.add(ctx.arcs[0]["rgb"])
    assert (1.0, 0, 0) in seen
    assert (0.3, 0, 0) in seen
