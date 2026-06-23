from app.leds import (
    LedRing,
    fill_frame,
    fill_up_frame,
    FLASH_DURATION_MS,
    FLASH_GREEN,
    FLASH_RED,
    LED_COUNT,
    TOP_LED,
)

BLUE = (0, 0, 40)
BOTTOM_LED = (TOP_LED + LED_COUNT // 2) % LED_COUNT


def test_fill_starts_at_the_top():
    frame = fill_frame(BLUE, FLASH_RED, 0.0)
    assert frame[TOP_LED] == FLASH_RED
    assert frame[BOTTOM_LED] == BLUE  # bottom not reached yet


def test_fill_is_symmetric_about_the_top():
    frame = fill_frame(BLUE, FLASH_RED, 0.4)
    left = frame[(TOP_LED - 1) % LED_COUNT]
    right = frame[(TOP_LED + 1) % LED_COUNT]
    assert left == right  # both sides descend together


def test_fill_reaches_the_bottom_last():
    # The bottom LED only goes red once the two fronts meet there.
    early = fill_frame(BLUE, FLASH_RED, 0.3)
    done = fill_frame(BLUE, FLASH_RED, 1.0)
    assert early[BOTTOM_LED] == BLUE
    assert done[BOTTOM_LED] == FLASH_RED


def test_fill_is_complete_and_solid_at_full_progress():
    frame = fill_frame(BLUE, FLASH_RED, 1.0)
    assert all(led == FLASH_RED for led in frame)


def test_fill_up_starts_at_the_bottom():
    frame = fill_up_frame(BLUE, FLASH_GREEN, 0.0)
    assert frame[BOTTOM_LED] == FLASH_GREEN
    assert frame[TOP_LED] == BLUE  # top not reached yet


def test_fill_up_reaches_the_top_last():
    early = fill_up_frame(BLUE, FLASH_GREEN, 0.3)
    done = fill_up_frame(BLUE, FLASH_GREEN, 1.0)
    assert early[TOP_LED] == BLUE
    assert done[TOP_LED] == FLASH_GREEN
    assert all(led == FLASH_GREEN for led in done)


def _ring():
    writes = []
    ring = LedRing(lambda frame: writes.append(frame), count=LED_COUNT)
    return ring, writes


def test_set_base_writes_solid_colour():
    ring, writes = _ring()
    ring.set_base(BLUE)
    assert writes[-1] == [BLUE] * LED_COUNT


def test_update_is_silent_without_a_flash():
    ring, writes = _ring()
    ring.update(1000)
    assert writes == []


def test_flash_animates_then_settles_to_base():
    ring, writes = _ring()
    ring.set_base(BLUE)
    ring.flash(FLASH_GREEN, now_ms=0)

    ring.update(FLASH_DURATION_MS // 2)
    assert writes[-1] != [BLUE] * LED_COUNT  # mid-flash: animation on the ring

    ring.update(FLASH_DURATION_MS + 1)
    assert writes[-1] == [BLUE] * LED_COUNT  # done: back to identity colour

    before = len(writes)
    ring.update(FLASH_DURATION_MS + 100)
    assert len(writes) == before  # steady again — no further bus traffic


def test_flash_uses_the_given_animation():
    ring, writes = _ring()
    ring.set_base(BLUE)
    ring.flash(FLASH_RED, now_ms=0, frame_fn=fill_frame)
    ring.update(1)  # one tick into the flash
    assert writes[-1] == fill_frame(BLUE, FLASH_RED, 1 / FLASH_DURATION_MS)


def test_set_base_during_flash_defers_until_it_settles():
    ring, writes = _ring()
    ring.flash(FLASH_GREEN, now_ms=0)
    ring.set_base(BLUE)  # must not stomp the running animation
    assert writes == []
    ring.update(FLASH_DURATION_MS + 1)
    assert writes[-1] == [BLUE] * LED_COUNT
