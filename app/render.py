import time

from app_components import clear_background

from .constants import BADGE_COLOURS


class Renderer:
	def __init__(self, app):
		self.app = app
		self._qr_matrix = None

	def draw(self, ctx):
		app = self.app
		ctx.save()
		clear_background(ctx)
		ctx.text_align = ctx.CENTER
		ctx.text_baseline = ctx.MIDDLE

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

		if app.notification:
			app.notification.draw(ctx)
		ctx.restore()

	def _instruction_fraction(self):
		s = self.app.session
		if s.display_time_remaining_s is None or s.display_timeout_s is None or s.display_updated_ms is None:
			return None
		elapsed_s = time.ticks_diff(time.ticks_ms(), s.display_updated_ms) / 1000
		remaining = s.display_time_remaining_s - elapsed_s
		return max(0.0, min(1.0, remaining / s.display_timeout_s))

	def _draw_waiting(self, ctx):
		session = self.app.session
		ctx.rgb(0, 1, 0)
		ctx.font_size = 16
		ctx.move_to(0, -68).text("Room {}".format(session.room_id))

		players = session.players
		if players:
			y = -52
			for player in players:
				colour = player.get("colour", "")
				name = player.get("username") or colour
				rgb = BADGE_COLOURS.get(colour, (20, 20, 20))
				ctx.rgb(rgb[0] / 40, rgb[1] / 40, rgb[2] / 40)
				ctx.font_size = 11
				ctx.move_to(0, y).text(name)
				y += 12
		else:
			ctx.rgb(0, 1, 0)
			ctx.font_size = 14
			ctx.move_to(0, -40).text("{} badge{} connected".format(
				session.badge_count,
				"s" if session.badge_count != 1 else "",
			))

		ctx.rgb(0, 1, 0)
		ctx.font_size = 16
		if session.ready_count > 0:
			ctx.move_to(0, 30).text("{} / {} ready".format(session.ready_count, session.badge_count))
			ctx.font_size = 12
			if session.is_ready:
				ctx.rgb(0, 0.6, 0)
				ctx.move_to(0, 48).text("you're ready!")
			else:
				ctx.rgb(0.8, 0.8, 0)
				ctx.move_to(0, 48).text("press any button")
		else:
			ctx.move_to(0, 30).text("Press any button")
			ctx.move_to(0, 48).text("to start round")
		ctx.font_size = 10
		ctx.rgb(0.5, 0.5, 0.5)
		ctx.move_to(0, 68).text("hold cancel to leave")

	def _draw_in_round(self, ctx):
		app = self.app
		session = app.session
		ctx.rgb(0.4, 0.4, 0.4)
		ctx.font_size = 10
		ctx.move_to(0, -68).text("Room {}  Badge {}".format(session.room_id, app.badge_id[-6:]))
		ctx.rgb(0, 1, 0)
		if session.display_target_colour:
			ctx.font_size = 14
			ctx.move_to(0, -50).text(session.display_target_colour)
		ctx.font_size = 24
		ctx.move_to(0, -30).text(session.display_module_name or "")
		ctx.move_to(0, -4).text(session.display_command or "...")
		frac = self._instruction_fraction()
		if frac is not None:
			ctx.rgb(0.2, 0.2, 0.2)
			ctx.rectangle(-100, 10, 200, 5).fill()
			if frac > 0.5:
				ctx.rgb(0, 0.8, 0)
			elif frac > 0.25:
				ctx.rgb(0.8, 0.6, 0)
			else:
				ctx.rgb(0.8, 0.1, 0)
			ctx.rectangle(-100, 10, 200 * frac, 5).fill()
		ctx.rgb(0, 1, 0)
		ctx.font_size = 30
		ctx.move_to(0, 52).text(session.format_remaining(time.ticks_ms()))
		ctx.font_size = 10
		ctx.rgb(0.5, 0.5, 0.5)
		modules = ", ".join(m.friendly_name() for m in app.module_registry.connected_modules())
		ctx.move_to(0, 72).text(modules or "No modules")

	def _draw_testing_command(self, ctx):
		ts = self.app._test_session
		ctx.rgb(0, 1, 0)
		ctx.font_size = 12
		ctx.move_to(0, -68).text("Testing {}/{}".format(ts.index + 1, ts.total))
		ctx.font_size = 24
		ctx.move_to(0, -30).text(ts.current_module.friendly_name())
		ctx.move_to(0, -4).text(ts.current_command)
		ctx.font_size = 10
		ctx.rgb(0.5, 0.5, 0.5)
		ctx.move_to(0, 68).text("hold cancel to skip")

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
		ctx.move_to(0, 70).text("hold cancel to finish")

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

		ctx.rgb(0.4, 0.8, 0.4)
		ctx.font_size = 13
		ctx.move_to(0, -75).text("Round over!")

		ctx.rgb(0, 1, 0)
		if overall is not None:
			ctx.font_size = 11
			ctx.move_to(0, -52).text("Your team's score")
			ctx.font_size = 38
			ctx.move_to(0, -26).text("{}".format(overall))
			ctx.font_size = 14
			ctx.move_to(0, -3).text("{} pass  {} fail".format(
				scores.get("passed", 0), scores.get("failed", 0),
			))
		else:
			ctx.font_size = 22
			ctx.move_to(0, -40).text("{} pass".format(scores.get("passed", 0)))
			ctx.move_to(0, -16).text("{} fail".format(scores.get("failed", 0)))

		badge_scores = session.badge_scores
		if badge_scores:
			colour_names = {p["colour"]: p.get("username") or p["colour"] for p in session.players}
			ctx.font_size = 11
			ctx.text_align = ctx.LEFT
			y = 10
			for colour in sorted(badge_scores):
				s = badge_scores[colour]
				marker = "*" if colour == session.badge_colour else " "
				name_part = "{}{}".format(marker, colour_names.get(colour, colour))
				score_part = ": {} / {}".format(s.get("passed", 0), s.get("failed", 0))
				x = -(ctx.text_width(name_part) + ctx.text_width(score_part)) / 2
				rgb = BADGE_COLOURS.get(colour, (20, 20, 20))
				ctx.rgb(rgb[0] / 40, rgb[1] / 40, rgb[2] / 40)
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
				ctx.move_to(0, 82).text("you're ready")
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
