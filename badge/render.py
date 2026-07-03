import math
import time

from app_components import clear_background

from .constants import BADGE_COLOURS, COLOURS


# The instruction ring sits inside the cancel-hold ring (radius 116, width 5)
# so the two stay readable when both are on screen.
RING_RADIUS = 105
RING_WIDTH = 10
RING_YELLOW_FRACTION = 2 / 3  # ring: green above this, yellow below
RING_RED_FRACTION = 1 / 3     # ring: red and blinking below this

# The scheduler renders at most 20fps (one frame per update tick, 50ms
# minimum), and the websocket + I2C polling share that loop, so effective
# frame rate is lower and uneven. Continuous tweens judder at that rate;
# every effect here is therefore a discrete state change, which stays crisp
# however few frames it gets.
SPLASH_MAX_MS = 2000  # result screen safety cap if no new instruction arrives
ROUND_PANIC_S = 10  # final round seconds: once-a-second beat
BEAT_MS = 250     # how much of each panic second the beat state is on

# The colour banner: a full-brightness chord segment across the top of the
# screen, sitting inside the ring (radius < ring inner edge 100). Full
# saturation keeps purple/blue distinguishable where a dim wash muddied
# them, and the text below stays on black. In-round it names the target;
# in the waiting room it teaches players their own colour.
BANNER_RADIUS = 95
BANNER_CHORD_Y = -44  # banner's bottom edge; module name starts at -42
BANNER_TEXT_Y = -70

# The waiting screen has no timer ring to stay inside, so its banner is
# larger and sits higher: clear of the lobby's top slot (own-ring top edge
# ~-43) with even ~9px margins around the label and word.
WAIT_BANNER_RADIUS = 108
WAIT_BANNER_CHORD_Y = -50
WAIT_BANNER_WORD_Y = -70
WAIT_BANNER_LABEL_Y = -94

# Waiting-room lobby: one slot per badge colour arranged in a hexagon.
LOBBY_CENTER_Y = 10
LOBBY_RADIUS = 40
LOBBY_DOT_R = 9

COUNT_UP_MS = 1200  # finished screen: team score ticks up from zero


class Renderer:
	def __init__(self, app):
		self.app = app
		self._qr_matrix = None
		self._splash = None  # (passed, start_ms) while the result flash runs
		self._finished_ms = None  # when the finished screen first showed

	def draw(self, ctx):
		app = self.app
		ctx.save()
		clear_background(ctx)
		ctx.text_align = ctx.CENTER
		ctx.text_baseline = ctx.MIDDLE

		if app._os_unsupported:
			self._draw_os_unsupported(ctx)
			ctx.restore()
			return

		# Anchor for the finished screen's count-up: the moment the screen
		# first appears, cleared when the room moves on.
		if app.session.room_state == "finished":
			if self._finished_ms is None:
				self._finished_ms = time.ticks_ms()
		else:
			self._finished_ms = None

		if app._test_session is not None:
			if app._test_session.state == "command":
				self._draw_testing_command(ctx)
			elif app._test_session.state == "waiting":
				self._draw_testing_waiting(ctx)
			elif app._test_session.state == "summary":
				self._draw_testing_summary(ctx)
		elif app.session.room_state == "waiting":
			self._draw_waiting(ctx)
		elif app.session.room_state == "in-round":
			self._draw_in_round(ctx)
		elif app.session.room_state == "finished":
			self._draw_finished(ctx)
		elif app._qr_active:
			self._draw_qr_screen(ctx)
		elif app.menu:
			app.menu.draw(ctx)

		if app._test_session is not None:
			hold = app._test_session.cancel_hold_progress(time.ticks_ms())
		elif app.session.in_game:
			hold = app.session.cancel_hold_progress(time.ticks_ms())
		else:
			hold = None
		if hold is not None:
			self._draw_hold_progress(ctx, hold)

		if app.notification:
			app.notification.draw(ctx)
		ctx.restore()

	def _draw_os_unsupported(self, ctx):
		ctx.rgb(0, 1, 0)
		ctx.font_size = 24
		ctx.move_to(0, -20).text("tildagonOS")
		ctx.font_size = 20
		ctx.rgb(0.85, 0.85, 0.85)
		ctx.move_to(0, 8).text("v2 required")
		ctx.font_size = 11
		ctx.rgb(0.5, 0.5, 0.5)
		ctx.move_to(0, 40).text("please update your badge")
		ctx.font_size = 9
		ctx.move_to(0, 80).text("any key to exit")

	def _draw_hold_progress(self, ctx, frac):
		# Ring around the rim that fills as the cancel button is held, so the
		# hold gives live feedback instead of silently firing at the timeout.
		ctx.save()
		ctx.line_width = 5
		ctx.rgb(0.95, 0.55, 0)
		start = -math.pi / 2
		ctx.arc(0, 0, 116, start, start + 2 * math.pi * frac, False)
		ctx.stroke()
		ctx.restore()

	def flash_result(self, passed, now_ms):
		# Landing the pass/fail where the player is actually looking: the LED
		# comet plays on the ring, this plays on the screen.
		self._splash = (passed, now_ms)

	def _draw_result_splash(self, ctx):
		if self._splash is None:
			return
		passed, start = self._splash
		# The result screen holds until the next instruction arrives, so
		# there's never a gap showing a stale instruction. The safety cap
		# stops a quiet server from hiding the round forever; a cleared
		# display (left room / round reset) also drops it.
		changed = self.app.session.display_changed_ms
		if (
			changed is None
			or time.ticks_diff(changed, start) > 0
			or time.ticks_diff(time.ticks_ms(), start) >= SPLASH_MAX_MS
		):
			self._splash = None
			return
		if passed:
			bg, fg, word = (0, 0.30, 0), (0.5, 1, 0.5), "NICE!"
		else:
			bg, fg, word = (0.35, 0, 0), (1, 0.45, 0.45), "MISS!"
		ctx.save()
		ctx.rgb(*bg)
		ctx.rectangle(-120, -120, 240, 240).fill()
		ctx.rgb(*fg)
		ctx.font_size = 42
		ctx.move_to(0, 0).text(word)
		ctx.restore()

	def _draw_colour_banner(self, ctx, colour_name, label=None,
			radius=BANNER_RADIUS, chord_y=BANNER_CHORD_Y,
			word_y=BANNER_TEXT_Y, label_y=None):
		# Chord segment across the top in the full-brightness colour, with
		# the colour word knocked out of it in black or white (whichever
		# contrasts with that colour). An optional small label sits above
		# the word ("YOU ARE" in the waiting room).
		rgb = self._colour_rgb(colour_name)
		ctx.save()
		ctx.rgb(*rgb)
		a = math.asin(chord_y / radius)
		ctx.arc(0, 0, radius, math.pi - a, 2 * math.pi + a, False)
		ctx.fill()
		luma = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
		if luma > 0.5:
			ctx.rgb(0, 0, 0)
		else:
			ctx.rgb(1, 1, 1)
		if label and label_y is not None:
			ctx.font_size = 10
			ctx.move_to(0, label_y).text(label)
		ctx.font_size = 22
		word = colour_name[0].upper() + colour_name[1:] if colour_name else ""
		ctx.move_to(0, word_y).text(word)
		ctx.restore()

	def _colour_rgb(self, colour_name):
		# Screen colour for a badge colour name, normalised so the brightest
		# channel hits 1.0 — keeps the hue but reads at full brightness on
		# the dark background. Green fallback matches the app's default
		# text colour.
		rgb = BADGE_COLOURS.get((colour_name or "").lower())
		if not rgb:
			return (0, 1, 0)
		peak = max(rgb)
		return (rgb[0] / peak, rgb[1] / peak, rgb[2] / peak)

	def _draw_instruction_ring(self, ctx, frac):
		# Purely the instruction timer — the background wash carries the
		# target colour. Traffic-light coded by remaining thirds: green,
		# yellow, then red and blinking. No dim trace behind it — a second
		# full-circle stroke measured ~8ms/frame on hardware.
		if frac <= 0:
			return
		ctx.save()
		ctx.line_width = RING_WIDTH
		if frac > RING_YELLOW_FRACTION:
			ctx.rgb(0, 1, 0)
		elif frac > RING_RED_FRACTION:
			ctx.rgb(0.9, 0.9, 0)
		else:
			bright = 1.0 if (time.ticks_ms() // 250) % 2 else 0.3
			ctx.rgb(bright, 0, 0)
		start = -math.pi / 2
		ctx.arc(0, 0, RING_RADIUS, start, start + 2 * math.pi * frac, False)
		ctx.stroke()
		ctx.restore()

	def _instruction_fraction(self):
		s = self.app.session
		if s.display_time_remaining_s is None or s.display_timeout_s is None or s.display_updated_ms is None:
			return None
		elapsed_s = time.ticks_diff(time.ticks_ms(), s.display_updated_ms) / 1000
		remaining = s.display_time_remaining_s - elapsed_s
		return max(0.0, min(1.0, remaining / s.display_timeout_s))

	def _draw_cancel_hint(self, ctx, action, text_y=-58):
		# Reminder placed in the top-left corner next to the physical
		# cancel button: a pointer plus what holding it does on this
		# screen (leave / skip / finish). text_y lets screens with a
		# banner across the top drop the words below it.
		ctx.save()
		ctx.rgb(0.5, 0.5, 0.5)
		ctx.move_to(-95, -75).line_to(-80, -67).line_to(-87, -60).fill()
		ctx.text_align = ctx.LEFT
		ctx.font_size = 10
		ctx.move_to(-84, text_y).text("Hold to")
		ctx.move_to(-84, text_y + 12).text(action)
		ctx.restore()

	def _draw_waiting(self, ctx):
		session = self.app.session

		# Until the websocket join round-trip completes we have no server state
		# (player list, badge count), so show a connecting placeholder rather
		# than the misleading "0 badges connected".
		if not self.app.net.joined:
			ctx.rgb(0, 1, 0)
			ctx.font_size = 16
			ctx.move_to(0, -30).text("Room {}".format(session.room_id))
			ctx.move_to(0, 0).text("Connecting...")
			self._draw_cancel_hint(ctx, "leave")
			return

		# The banner teaches the one fact the game depends on: which colour
		# is you when someone shouts it.
		if session.badge_colour:
			self._draw_colour_banner(
				ctx, session.badge_colour, label="YOU ARE",
				radius=WAIT_BANNER_RADIUS, chord_y=WAIT_BANNER_CHORD_Y,
				word_y=WAIT_BANNER_WORD_Y, label_y=WAIT_BANNER_LABEL_Y,
			)

		ctx.rgb(0.5, 0.5, 0.5)
		ctx.font_size = 12
		ctx.move_to(0, LOBBY_CENTER_Y - 4).text("Room {}".format(session.room_id))

		self._draw_lobby(ctx, session)

		ctx.rgb(0, 1, 0)
		ctx.font_size = 14
		ctx.move_to(0, 74).text("{} / {} ready".format(session.ready_count, session.badge_count))
		ctx.font_size = 10
		if session.is_ready:
			ctx.rgb(0, 0.6, 0)
			ctx.move_to(0, 88).text("press to unready")
		else:
			ctx.rgb(0.8, 0.8, 0)
			ctx.move_to(0, 88).text("press any button")

	def _draw_lobby(self, ctx, session):
		# One fixed slot per badge colour, arranged in a hexagon around the
		# room number: filled dot = that badge is ready, outline = joined
		# but not ready, faint outline = empty slot. Your own slot is
		# ringed in white. Colour = identity, matching the LED rings.
		players = {}
		for player in session.players or []:
			players[player.get("colour")] = player
		ctx.save()
		for i, colour in enumerate(COLOURS):
			angle = -math.pi / 2 + i * math.pi / 3
			x = LOBBY_RADIUS * math.cos(angle)
			y = LOBBY_CENTER_Y + LOBBY_RADIUS * math.sin(angle)
			player = players.get(colour)
			if player is None:
				ctx.rgb(0.18, 0.18, 0.18)
				ctx.line_width = 2
				ctx.arc(x, y, LOBBY_DOT_R, 0, 2 * math.pi, False)
				ctx.stroke()
				continue
			ctx.rgb(*self._colour_rgb(colour))
			if player.get("ready"):
				ctx.arc(x, y, LOBBY_DOT_R, 0, 2 * math.pi, False)
				ctx.fill()
			else:
				ctx.line_width = 3
				ctx.arc(x, y, LOBBY_DOT_R, 0, 2 * math.pi, False)
				ctx.stroke()
			if colour == session.badge_colour:
				ctx.rgb(1, 1, 1)
				ctx.line_width = 2
				ctx.arc(x, y, LOBBY_DOT_R + 4.5, 0, 2 * math.pi, False)
				ctx.stroke()
		ctx.restore()

	def _draw_in_round(self, ctx):
		app = self.app
		session = app.session
		now = time.ticks_ms()
		secs = session.remaining_seconds(now)

		# Final seconds of the round: a once-a-second beat — background flash
		# plus a bigger countdown, drawn below. The banner draws over it.
		beat = secs is not None and 0 < secs <= ROUND_PANIC_S and (now % 1000) < BEAT_MS
		if beat:
			ctx.rgb(0.30, 0, 0)
			ctx.rectangle(-120, -120, 240, 240).fill()

		# No room/badge header in-round: ctx text costs ~0.9ms per character
		# (size-independent), and that 20-char line of lobby info was ~18ms of
		# a 50ms frame budget.
		if session.display_target_colour:
			self._draw_colour_banner(ctx, session.display_target_colour)

		frac = self._instruction_fraction()

		ctx.rgb(0, 1, 0)
		ctx.font_size = 24
		ctx.move_to(0, -30).text(session.display_module_name or "")
		ctx.move_to(0, -4).text(session.display_instruction or "...")

		if frac is not None:
			self._draw_instruction_ring(ctx, frac)

		# No modules footer in-round either — same reasoning as the header:
		# ~25 characters of lobby info for ~18ms a frame.
		if secs is None or secs > 30:
			ctx.rgb(0, 1, 0)
		elif secs > ROUND_PANIC_S:
			ctx.rgb(0.95, 0.55, 0)
		else:
			ctx.rgb(1, 0, 0)
		ctx.font_size = 38 if beat else 30
		ctx.move_to(0, 52).text(session.format_remaining(now))

		self._draw_result_splash(ctx)

	def _draw_testing_command(self, ctx):
		ts = self.app._test_session
		ctx.rgb(0, 1, 0)
		ctx.font_size = 12
		ctx.move_to(0, -68).text("Testing {}/{}".format(ts.index + 1, ts.total))
		ctx.font_size = 24
		ctx.move_to(0, -30).text(ts.current_module.friendly_name())
		ctx.move_to(0, -4).text(ts.current_instruction or ts.current_command)
		self._draw_cancel_hint(ctx, "skip")

	def _draw_testing_waiting(self, ctx):
		ts = self.app._test_session
		ctx.rgb(0, 1, 0)
		ctx.font_size = 18
		ctx.move_to(0, -34).text("Waiting for")
		ctx.move_to(0, -12).text("commands...")
		ctx.font_size = 11
		ctx.rgb(0.5, 0.8, 0.5)
		ctx.move_to(0, 16).text("{} passed  {} skipped".format(ts.passed, ts.skipped))
		ctx.font_size = 10
		ctx.rgb(0.5, 0.5, 0.5)
		ctx.move_to(0, 56).text("interact with hexpansion")
		self._draw_cancel_hint(ctx, "finish")

	def _draw_testing_summary(self, ctx):
		ts = self.app._test_session
		ctx.rgb(0, 1, 0)
		ctx.font_size = 20
		ctx.move_to(0, -40).text("Test complete!")
		ctx.font_size = 16
		ctx.move_to(0, -10).text("{} passed".format(ts.passed))
		ctx.move_to(0, 15).text("{} skipped".format(ts.skipped))
		ctx.font_size = 10
		ctx.rgb(0.5, 0.5, 0.5)
		ctx.move_to(0, 55).text("press any button")

	def _draw_finished(self, ctx):
		session = self.app.session
		scores = session.server_scores
		overall = session.overall_score
		elapsed = 0
		if self._finished_ms is not None:
			elapsed = time.ticks_diff(time.ticks_ms(), self._finished_ms)

		ctx.rgb(0.4, 0.8, 0.4)
		ctx.font_size = 13
		ctx.move_to(0, -75).text("Round over!")

		ctx.rgb(0, 1, 0)
		if overall is not None:
			ctx.font_size = 11
			ctx.move_to(0, -52).text("Your team's score")
			ctx.font_size = 38
			# The score ticks up from zero — integer steps, so it stays
			# crisp at any frame rate — and lands on the exact value.
			if elapsed < COUNT_UP_MS:
				shown = int(overall * elapsed / COUNT_UP_MS)
			else:
				shown = overall
			ctx.move_to(0, -26).text("{}".format(shown))
			ctx.font_size = 14
			ctx.move_to(0, -3).text("{} pass  {} fail".format(
				scores.get("passed", 0), scores.get("failed", 0),
			))
			# Rank reveal lands after the count-up, as a second beat.
			# The server computes it over the last 24 hours.
			if session.rank is not None and elapsed >= COUNT_UP_MS:
				ctx.font_size = 10
				ctx.rgb(0.85, 0.75, 0.2)
				ctx.move_to(0, 10).text("#{} of {} today".format(
					session.rank, session.total_games or session.rank,
				))
		else:
			ctx.font_size = 22
			ctx.move_to(0, -40).text("{} pass".format(scores.get("passed", 0)))
			ctx.move_to(0, -16).text("{} fail".format(scores.get("failed", 0)))

		badge_scores = session.badge_scores
		if badge_scores:
			colour_names = {p["colour"]: p.get("username") or p["colour"] for p in session.players}
			best = max(s.get("passed", 0) for s in badge_scores.values())
			ctx.font_size = 11
			ctx.text_align = ctx.LEFT
			y = 24
			for colour in sorted(badge_scores):
				s = badge_scores[colour]
				name_part = colour_names.get(colour, colour)
				score_part = ": {} / {}".format(s.get("passed", 0), s.get("failed", 0))
				x = -(ctx.text_width(name_part) + ctx.text_width(score_part)) / 2
				rgb = BADGE_COLOURS.get(colour, (20, 20, 20))
				ctx.rgb(rgb[0] / 40, rgb[1] / 40, rgb[2] / 40)
				if best > 0 and s.get("passed", 0) == best:
					# Crown the top scorer(s): a small triangle in their
					# colour to the left of the name.
					ctx.move_to(x - 9, y + 3).line_to(x - 5, y - 4).line_to(x - 1, y + 3).fill()
				ctx.move_to(x, y).text(name_part)
				ctx.rgb(0.5, 0.8, 0.5)
				ctx.move_to(x + ctx.text_width(name_part), y).text(score_part)
				y += 13
			ctx.text_align = ctx.CENTER

		ctx.font_size = 14
		if session.dismissed_count > 0:
			ctx.rgb(0, 1, 0)
			ctx.move_to(0, 68).text("{} / {} ready".format(session.dismissed_count, session.badge_count))
			ctx.font_size = 10
			if session.is_dismissed:
				ctx.rgb(0, 0.6, 0)
				ctx.move_to(0, 82).text("press to unready")
			else:
				ctx.rgb(0.8, 0.8, 0)
				ctx.move_to(0, 82).text("press any button")
		else:
			ctx.font_size = 10
			ctx.rgb(0.4, 0.4, 0.4)
			ctx.move_to(0, 74).text("press any button to continue")

	def _draw_qr_screen(self, ctx):
		matrix = self._get_qr_matrix()
		if matrix:
			qr_size = len(matrix)
			pixel_size = max(3, int(160 / qr_size))
			total = pixel_size * qr_size
			ox = -(total // 2)
			oy = -(total // 2) - 10
			ctx.rgb(1, 1, 1).rectangle(ox - 4, oy - 4, total + 8, total + 8).fill()
			for row in range(qr_size):
				for col in range(qr_size):
					if matrix[row][col]:
						ctx.rgb(0, 0, 0).rectangle(
							ox + col * pixel_size,
							oy + row * pixel_size,
							pixel_size, pixel_size,
						).fill()
		else:
			ctx.rgb(0, 1, 0)
			ctx.font_size = 11
			ctx.move_to(0, -20).text("Scan to set name")
			ctx.font_size = 9
			url = self.app.room_client.server_url
			ctx.move_to(0, 0).text(url + "/register/")
			ctx.move_to(0, 14).text(self.app._secret_id)
		ctx.rgb(0.5, 0.5, 0.5)
		ctx.font_size = 9
		ctx.move_to(0, 80).text("any key to go back")

	def _get_qr_matrix(self):
		if self._qr_matrix is None:
			try:
				from .lib.uQR import QRCode
				qr = QRCode()
				qr.add_data("{}/register/{}".format(self.app.room_client.server_url, self.app._secret_id))
				self._qr_matrix = qr.get_matrix()
			except Exception:
				self._qr_matrix = False
		return self._qr_matrix if self._qr_matrix else None
