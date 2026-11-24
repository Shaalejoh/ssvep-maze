"""
Playing one maze.

Every frame the buttons flicker. Every 0.1 s the latest EEG window is scored, rescaled with the
calibration, and fed to the EvidenceAccumulator. When it picks a direction, the player moves
(or bumps into a wall), the decision is logged, and the accumulator starts over.

Keys: SPACE pause/resume, ESC pause menu, X "the last move wasn't what I meant",
      arrow keys move too if the game was started with --keyboard (logged separately).
"""
import collections
import time

import numpy as np
import pygame

from .decision import EvidenceAccumulator
from .maze import step
from .screen import draw_star, format_time
from .settings import (BAD_RED, DIM_GREY, DIRECTIONS, FOG_SIGHT_STEPS, GOLD, GOOD_GREEN, MARKER, SCORE_INTERVAL_S,
                       SPEED_PRESETS, WARN_AFTER_PROBLEMS, WARN_WINDOW_MOVES)

ARROW_KEYS = {pygame.K_UP: "up", pygame.K_RIGHT: "right", pygame.K_DOWN: "down", pygame.K_LEFT: "left"}
PAUSE_MENU = ["Resume", "Restart this maze", "New maze", "Main menu", "Quit game"]
PAUSE_OUTCOME = {"Restart this maze": "restart", "New maze": "new", "Main menu": "menu", "Quit game": "quit"}


class Level:
    def __init__(self, game, maze):
        self.game, self.maze, self.args = game, maze, game.args
        self.settings = dict(game.settings)          # frozen for this level
        if SPEED_PRESETS[self.settings["speed"]].window_s not in game.levels:
            print(f"{self.settings['speed']} needs a calibration from this version of the game; using Normal")
            self.settings["speed"] = game.settings["speed"] = "Normal"
        preset = SPEED_PRESETS[self.settings["speed"]]
        self.margin = self.args.evidence if self.args.evidence else preset.margin
        self.window_s = preset.window_s
        self.scorer = game.scorers[preset.window_s]
        self.reference = game.levels[preset.window_s]
        self.accumulator = EvidenceAccumulator(self.margin, preset.pause_s, self.args.horizon, self.args.floor)

        game.level_number += 1
        self.number = game.level_number
        self.pos, self.trail = maze.start, [maze.start]
        self.fog = self.settings["fog"]
        self.visible = maze.visible_from(self.pos, FOG_SIGHT_STEPS) if self.fog else None
        self.stats = {"level": self.number, **self.settings, "A": self.margin, "window_s": preset.window_s,
                      "dead_s": preset.pause_s, "seed": maze.seed, "optimal_moves": maze.shortest_route,
                      "dead_ends": maze.dead_ends, "decisions": 0, "moves": 0, "bumps": 0, "toward_goal": 0,
                      "guided": 0, "guided_correct": 0, "keyboard_moves": 0, "flagged": 0, "decision_times": []}
        self.problems = collections.deque(maxlen=WARN_WINDOW_MOVES)   # guided errors / X flags, for the warning
        self.rejected = collections.deque(maxlen=30)                  # artifact flags over the last 3 s
        self.bump_flash_until = 0.0

    # ------------------------------------------------------------------ main loop
    def play(self):
        """Returns (outcome, stats); outcome is 'new', 'restart', 'menu' or 'quit'."""
        g, maze, v = self.game, self.maze, self.game.view
        v.render_maze(maze, self.pos, self.trail, self.visible)
        v.set_banner(None)
        if g.session_started is None:
            g.session_started = g.now()
            g.mark(MARKER["session_start"], "game_start")
        g.mark(MARKER["level_start"], "level_start", level=self.number, size=maze.n, difficulty=maze.difficulty,
               fog=int(self.fog), guide=int(self.settings["guide"]), speed=self.settings["speed"], A=self.margin,
               window_s=self.window_s, dead_s=self.stats["dead_s"], maze_seed=maze.seed)
        g.restart_frame_pacing()
        now = g.now()
        self.accumulator.reset(now)
        next_score_at, last_frame_at, self.active_time = now + SCORE_INTERVAL_S, now, 0.0
        self.paused = False

        while self.pos != maze.goal:
            target = maze.best_direction(self.pos)
            outcome = self.handle_keys(target)
            if outcome == "resumed":
                last_frame_at = g.now()
                continue
            if outcome is not None:
                return outcome, self.stats
            if self.pos == maze.goal:
                break
            g.look_at(None if self.paused else target)
            self.update_side_panels(last_frame_at)
            progress = self.accumulator.progress() if not self.paused else None
            show_guide = self.settings["guide"] and not self.paused
            now = g.frame(cue=target if show_guide else None, flickering=not self.paused, progress=progress)
            g.screenshot("play_fog" if self.fog else ("play_guide" if self.settings["guide"] else "play"))
            if not self.paused:
                self.active_time += now - last_frame_at
            last_frame_at = now
            if self.bump_flash_until and now >= self.bump_flash_until:
                self.bump_flash_until = 0.0
                v.render_maze(maze, self.pos, self.trail, self.visible)
            if self.paused or now < next_score_at:
                continue

            # ---- score the latest EEG window and maybe move
            next_score_at = max(next_score_at + SCORE_INTERVAL_S, now - 0.05)
            raw = self.scorer.scores(g.recorder.buf, self.args.reject_uv)
            self.rejected.append(raw is None)
            normalised = None if raw is None else self.reference.normalise(raw)
            choice = self.accumulator.update(now, normalised)
            if choice is not None:
                self.make_move(DIRECTIONS[choice], target, "bci", normalised, raw)
        return self.finish()

    def handle_keys(self, target):
        """Returns None to keep playing, 'resumed' after the pause menu, or the level's outcome."""
        g = self.game
        for key in g.take_keys():
            if key == pygame.K_SPACE:
                self.paused = not self.paused
                g.mark(MARKER["pause" if self.paused else "resume"], "pause" if self.paused else "resume",
                       level=self.number)
                g.view.set_banner("Paused: SPACE to resume" if self.paused else None)
                if not self.paused:
                    self.accumulator.reset(g.now())
            elif key == pygame.K_x and self.stats["decisions"] and not self.paused:
                self.stats["flagged"] += 1
                if self.problems:
                    self.problems[-1] = True
                g.mark(MARKER["flag_unintended"], "flag_unintended", level=self.number, decision=self.stats["decisions"])
            elif key == pygame.K_ESCAPE:
                return self.pause_menu()
            elif self.args.keyboard and not self.paused and key in ARROW_KEYS:
                self.make_move(ARROW_KEYS[key], target, "keyboard", None, None)
        return None

    def pause_menu(self):
        g = self.game
        if not self.paused:
            g.mark(MARKER["pause"], "pause", level=self.number)
        g.frame(flickering=False)
        choice = g.choose_option(["Paused"], [f"Level {self.number}: {self.maze.n} x {self.maze.n} {self.maze.difficulty}"],
                                 PAUSE_MENU, screenshot="pause_menu")
        g.restart_frame_pacing()
        if choice == "Resume":
            self.paused = False
            g.view.set_banner(None)
            g.mark(MARKER["resume"], "resume", level=self.number)
            self.accumulator.reset(g.now())
            return "resumed"
        g.mark(MARKER["level_abandoned"], "level_abandoned", level=self.number)
        g.frame(flickering=False)
        self.stats["result"] = "abandoned"
        self.stats["time_s"] = self.active_time
        g.log_level(self.stats)
        return PAUSE_OUTCOME[choice]

    def update_side_panels(self, last_frame_at):
        g, s, maze = self.game, self.stats, self.maze
        elapsed = self.active_time + (0 if self.paused else g.now() - last_frame_at)
        rejected = np.mean(self.rejected) if self.rejected else 0.0
        signal = "good" if len(self.rejected) < 10 or rejected < 0.15 else ("noisy" if rejected < 0.5 else "poor")
        left = [f"Level {self.number}", f"{maze.n} x {maze.n}  {maze.difficulty}" + ("  fog" if self.fog else ""),
                "", f"Time   {format_time(elapsed)}", f"Moves  {s['moves']}   (shortest {maze.shortest_route})",
                f"Bumps  {s['bumps']}" + (f"   flagged {s['flagged']}" if s["flagged"] else ""),
                "", f"Speed  {self.settings['speed']}",
                (f"Signal {signal}", GOOD_GREEN if signal == "good" else BAD_RED)]
        if self.settings["guide"] and s["guided"]:
            left.append(f"Guide  {100 * s['guided_correct'] / s['guided']:.0f}% correct")
        if self.settings["speed"] in ("Fast", "Turbo") and sum(self.problems) >= WARN_AFTER_PROBLEMS:
            left += ["", (f"{sum(self.problems)} errors/flags in the last {len(self.problems)} moves:", BAD_RED),
                     ("consider Normal (ESC, Main menu)", BAD_RED)]
        left += ["", ("SPACE pause   ESC menu", DIM_GREY), ("X = last move wasn't me", DIM_GREY)]
        record = g.records.get(g.record_key(self.settings))
        right = [("Best", GOLD), format_time(record["best_time_s"]) + f"   {record['best_stars']}/3 stars"] if record else []
        g.view.set_hud(left, right)

    # ------------------------------------------------------------------ one move
    def make_move(self, direction, target, source, normalised, raw):
        """Apply a decision (from the headset or the keyboard), log it, and restart the accumulator."""
        g, maze, s = self.game, self.maze, self.stats
        now = g.now()
        leader_total, lead, leader_average = self.accumulator.stats()
        decision_time = now - self.accumulator.started_at
        s["decisions"] += 1
        closer = maze.gets_closer(self.pos, direction)
        guided = self.settings["guide"]
        correct = ""
        if guided:
            s["guided"] += 1
            correct = int(closer)                      # any direction on a shortest route counts as right
            s["guided_correct"] += correct
        self.problems.append(bool(guided and not correct))
        if source == "keyboard":
            s["keyboard_moves"] += 1
        else:
            s["decision_times"].append(decision_time)

        self.bump_flash_until = 0.0
        if maze.is_open(self.pos, direction):
            self.pos = step(self.pos, direction)
            self.trail.append(self.pos)
            s["moves"] += 1
            s["toward_goal"] += int(closer)
            outcome, code = "move", MARKER["move"] + DIRECTIONS.index(direction)
        else:
            s["bumps"] += 1
            outcome, code = "bump", MARKER["bump"] + DIRECTIONS.index(direction)
            self.bump_flash_until = now + 0.35
        g.mark(code, "feedback", predicted=direction, target=target if guided else "", outcome=outcome,
               correct=correct, level=self.number, toward_goal=int(closer), source=source)
        g.decisions.writerow([f"{now:.3f}", self.number, maze.n, maze.difficulty, int(self.fog), int(guided),
                              self.settings["speed"], self.margin, source, target if guided else "", direction, outcome,
                              int(closer), correct, f"{decision_time:.3f}", f"{leader_total:.3f}", f"{lead:.3f}",
                              f"{leader_average:.3f}",
                              "" if normalised is None else " ".join(f"{x:.4f}" for x in normalised),
                              "" if raw is None else " ".join(f"{x:.4f}" for x in raw), s["window_s"], s["dead_s"]])
        g.decisions_file.flush()
        self.accumulator.reset(now)
        if self.fog:
            self.visible |= maze.visible_from(self.pos, FOG_SIGHT_STEPS)
        g.view.render_maze(maze, self.pos, self.trail, self.visible, bumped=self.bump_flash_until > now)

    # ------------------------------------------------------------------ the end
    def finish(self):
        g, maze, s = self.game, self.maze, self.stats
        s["result"] = "complete"
        s["time_s"] = self.active_time
        wasted = s["moves"] - maze.shortest_route + s["bumps"]
        s["stars"] = 3 if wasted == 0 else (2 if wasted <= max(2, 0.2 * maze.shortest_route) else 1)
        s["efficiency"] = maze.shortest_route / max(1, s["moves"])
        g.mark(MARKER["level_complete"], "level_complete", level=self.number, time_s=round(self.active_time, 2))
        g.view.render_maze(maze, self.pos, self.trail, None)
        g.view.set_banner("Goal!", GOOD_GREEN)
        banner_until = time.perf_counter() + 1.5
        while time.perf_counter() < banner_until:
            g.frame(flickering=False)
        g.view.set_banner(None)
        s["new_best"] = g.save_records(self.settings, s)
        g.log_level(s)
        return self.results_screen(), s

    def results_screen(self):
        g, maze, s = self.game, self.maze, self.stats
        record = g.records[g.record_key(s)]
        best = "   NEW BEST!" if s["new_best"] else f"   (best {format_time(record['best_time_s'])})"
        lines = [f"{maze.n} x {maze.n} {maze.difficulty}" + ("  with fog" if s["fog"] else "")
                 + ("  with guide" if s["guide"] else ""), "",
                 f"Time {format_time(s['time_s'])}{best}",
                 f"Moves {s['moves']}   shortest possible {maze.shortest_route}   efficiency {100 * s['efficiency']:.0f}%",
                 f"Wall bumps {s['bumps']}   moves toward the goal {s['toward_goal']}/{s['moves']}"
                 + (f"   flagged unintended {s['flagged']}" if s["flagged"] else "")]
        if s["median_decision_s"]:
            lines.append(f"Median time per decision {s['median_decision_s']:.1f} s   ({s['bci_decisions']} decisions)")
        if s["guide"] and s["guided"]:
            lines.append(f"Followed the guide on {s['guided_correct']}/{s['guided']} decisions "
                         f"({100 * s['guided_correct'] / s['guided']:.0f}%)")

        def stars(surf, y):
            r = int(0.035 * g.screen.h)
            for i in range(3):
                draw_star(surf, (g.screen.w // 2 + (i - 1) * 3 * r, y + 2 * r), r, i < s["stars"])

        def show():
            g.text_screen(["Level complete"], lines, "SPACE next maze    R replay this maze    M menu    ESC quit", stars)
            g.screenshot("results")
        key = g.wait_for_key(show, (pygame.K_SPACE, pygame.K_RETURN, pygame.K_r, pygame.K_m, pygame.K_ESCAPE),
                             pygame.K_SPACE)
        g.restart_frame_pacing()
        return {pygame.K_SPACE: "new", pygame.K_RETURN: "new", pygame.K_r: "restart", pygame.K_m: "menu",
                pygame.K_ESCAPE: "quit"}[key]
