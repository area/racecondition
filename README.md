# Race Condition

**A co-operative shouting game for 1–6 players, played on [Tildagon](https://tildagon.badge.emfcamp.org/) badges**

Race Condition is a love letter to Spaceteam written through [EMFCamp](https://emfcamp.org)-tinted glasses. Everyone who wants to play together joins the same room, and everyone's screen shows an instruction meant for *someone else*. To clear it, you have to communicate it to the right player - and act on the instructions being shouted at *you* - before the timer runs out.

See the [site](https://racecondition.area.io) for more details.

## Project layout

| Path | What it is |
|---|---|
| `app.py` + `badge/` | The MicroPython badge app that ships to Tildagon badges |
| `server/` | The room server (rooms, rounds, scoring, leaderboard, web pages, API) |
| `hexpansion-firmwares/` | Submodule of hexpansion EEPROM manifests, used for friendly device names |
| `scripts/` | Dev tooling |
| `tests/` | Server and badge-app test suites |
---
