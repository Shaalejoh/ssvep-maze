"""
The game session: start-up, the frame loop, menus, and saving everything at the end.

One run of the program is one session. It records the EEG continuously from start to finish
(including menus) into data/sub-<subject>/game-<timestamp>/, with every event marked in the
recording. Calibration lives in calibration.py and a single maze in level.py.
"""
import csv
import json
import math
import os
import random
import time
from datetime import datetime

import numpy as np
import pygame

from .calibration import Calibration
from .headset import Recorder, SimulatedHeadset, board_channel_layout, open_board
from .level import Level
from .maze import Maze
from .screen import MazeView, QuitGame, Screen, draw_text_screen, format_time, menu_colours
from .settings import (BAD_RED, BLACK, DIFFICULTIES, DIFFICULTY_TEXT, DIM_GREY, DIRECTIONS, GOLD, GOOD_GREEN,
                       MAZE_SIZES, PHOTOSENSITIVITY_WARNING, SCORE_INTERVAL_S, SPEED_NAMES, SPEED_PRESETS,
                       WHITE, WINDOW_LENGTHS_S)
from .signal_processing import FlickerScorer, information_transfer_rate

DECISION_COLUMNS = ["t_s", "level", "size", "difficulty", "fog", "guide", "speed", "A", "source", "target",
                    "predicted", "outcome", "toward_goal", "correct", "decision_time_s", "evidence_lead",
                    "evidence_margin", "leader_mean", "norm_scores_up_right_down_left",
                    "raw_cca_up_right_down_left", "window_s", "dead_s"]


class Game:
    def __init__(self, args):
        self.args = args
        self.seed = args.seed if args.seed is not None else random.SystemRandom().randrange(1_000_000)
        self.rng = random.Random(self.seed)
        self.freq_of = dict(zip(DIRECTIONS, args.freqs))
        self.screen = Screen(args.windowed)
        self.view = MazeView(self.screen, args.button, not args.no_bars, args.maze_frac, args.gap, args.side_gap)

        # ---- where things are saved
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.subject_dir = os.path.join(args.out, f"sub-{args.subject}")
        self.folder = os.path.join(self.subject_dir, f"game-{stamp}")
        os.makedirs(self.folder, exist_ok=True)
        self.profile_path = os.path.join(self.subject_dir, "calibration.json")
        self.records_path = os.path.join(self.subject_dir, "maze_records.json")
        self.records = load_json(self.records_path, {})
        self.saved_profile = self._load_saved_profile()
        self.screenshots_taken = set()

        # ---- headset
        draw_text_screen(self.screen, ["SSVEP Maze"], ["Connecting to the headset..."])
        if args.board == "sim":
            self.board = SimulatedHeadset(args.freqs, self.seed)
            sample_rate, layout, board_id = SimulatedHeadset.sample_rate, SimulatedHeadset.layout, "sim"
        else:
            self.board, board_id = open_board(args)
            sample_rate, layout = board_channel_layout(board_id)
        self.recorder = Recorder(self.board, sample_rate, layout, args.channels, self.folder)
        self.scorers = {w: FlickerScorer(args.freqs, sample_rate, w) for w in WINDOW_LENGTHS_S}

        # ---- state
        self.settings = {"size": args.size, "difficulty": args.difficulty, "fog": args.fog, "guide": args.guide,
                         "speed": args.speed,
                         "calibration": "saved" if (args.use_saved_calibration and self.saved_profile) else "new"}
        self.levels = None                  # {window_s: ReferenceLevels} once calibrated
        self.calibration_source = None
        self.calibration_report = None
        self.meta = {"paradigm": "ssvep-maze-game", "subject": args.subject, "session": stamp, "board": args.board,
                     "board_id": board_id, "sampling_rate_hz": sample_rate, "channel_names": self.recorder.names,
                     "ssvep_channels": [self.recorder.names[i] for i in self.recorder.visual],
                     "freqs_hz": args.freqs, "directions": self.freq_of,
                     "controller": {"method": "evidence accumulation over per-button normalised CCA",
                                    "tick_s": SCORE_INTERVAL_S, "horizon_s": args.horizon, "floor": args.floor,
                                    "presets": {n: p.to_log() for n, p in SPEED_PRESETS.items()},
                                    "evidence_override": args.evidence, "reject_uv": args.reject_uv,
                                    "filter": "causal 6-45 Hz band-pass + 60 Hz notch"},
                     "layout": {"maze_frac": args.maze_frac, "gap": args.gap, "side_gap": args.side_gap,
                                "button": args.button, "screen_px": [self.screen.w, self.screen.h]},
                     "seed": self.seed, "refresh_hz": round(self.screen.refresh_hz, 3), "vsync": self.screen.vsync,
                     "raw_columns": self.recorder.header, "levels": [], "completed": False}
        self.decisions_file = open(os.path.join(self.folder, "decisions.csv"), "w", newline="")
        self.decisions = csv.writer(self.decisions_file)
        self.decisions.writerow(DECISION_COLUMNS)

        self.frame_number, self.dropped_frames, self.t_start = 0, 0, time.perf_counter()
        self.pace_start, self.pace_frame = time.perf_counter(), 0
        self.next_pull = 0.0
        self.pending_markers, self.keys = [], []
        self.level_number, self.session_started, self.level_log = 0, None, []
        self.recording_closed = False

    # ------------------------------------------------------------------ timing, input, data
    def now(self):
        """Seconds since the game started."""
        return time.perf_counter() - self.t_start

    def poll(self):
        """Collect key presses. Closing the window ends the session (everything is still saved)."""
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                raise QuitGame
            if ev.type == pygame.KEYDOWN:
                self.keys.append(ev.key)

    def take_keys(self):
        keys, self.keys = self.keys, []
        return keys

    def pull_data(self):
        """Fetch new EEG from the board every 0.1 s (also while menus are showing)."""
        if self.recording_closed:
            return
        now = self.now()
        if now >= self.next_pull:
            self.recorder.pull()
            self.next_pull = now + 0.1
        if self.args.max_seconds and now > self.args.max_seconds:
            raise QuitGame

    def restart_frame_pacing(self):
        """After a static screen, restart the clock used to pace frames when vsync isn't available."""
        self.pace_start, self.pace_frame = time.perf_counter(), self.frame_number

    def mark(self, code, kind, **info):
        """Queue an event marker; it is stamped into the EEG right after the next screen flip."""
        self.pending_markers.append((code, kind, info))

    def screenshot(self, name):
        if self.args.screenshots and name not in self.screenshots_taken:
            self.screenshots_taken.add(name)
            os.makedirs(self.args.screenshots, exist_ok=True)
            pygame.image.save(self.screen.surface, os.path.join(self.args.screenshots, f"{name}.png"))

    def button_brightness(self, flickering):
        """Each button's brightness (0-1) for this frame: a sine wave at its frequency, or steady grey."""
        if not flickering:
            return {d: 0.5 for d in DIRECTIONS}
        phase = 2 * math.pi * self.frame_number * self.screen.frame_s
        return {d: 0.5 * (1 + math.sin(self.freq_of[d] * phase)) for d in DIRECTIONS}

    def frame(self, cue=None, flickering=True, progress=None):
        """Draw one frame of the maze screen, stamp any queued markers, fetch data. Returns the time."""
        self.poll()
        if not self.screen.vsync:                         # no vsync: pace frames with the clock
            wait = self.pace_start + (self.frame_number - self.pace_frame) * self.screen.frame_s - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
        before = time.perf_counter()
        flipped_at = self.view.draw(self.button_brightness(flickering), cue, progress)
        for code, kind, info in self.pending_markers:
            self.recorder.mark(code, kind, **info)
        self.pending_markers = []
        if self.screen.vsync and self.frame_number > 0 and flipped_at - before > 1.5 * self.screen.frame_s:
            self.dropped_frames += 1
        self.frame_number += 1
        self.pull_data()
        return self.now()

    def look_at(self, direction):
        """Tell the simulated headset where the simulated player is looking (does nothing with real hardware)."""
        if isinstance(self.board, SimulatedHeadset):
            self.board.look_at(None if direction is None else self.freq_of[direction])

    # ------------------------------------------------------------------ static screens
    def text_screen(self, title, lines, hint=None, extra=None):
        draw_text_screen(self.screen, title, lines, hint, extra)

    def wait_for_key(self, draw, keys, default=None):
        """Keep redrawing a static screen (EEG keeps recording) until one of `keys` is pressed."""
        if self.args.autostart:
            draw()
            return default
        self.take_keys()
        while True:
            self.poll()
            draw()
            self.pull_data()
            for k in self.take_keys():
                if k in keys:
                    return k
            time.sleep(0.015)

    def choose_option(self, title, lines, options, screenshot=None):
        """A vertical list of choices; returns the chosen one (ESC picks the first)."""
        if self.args.autostart:
            return options[0]
        selected = 0
        self.take_keys()
        while True:
            self.poll()
            for k in self.take_keys():
                if k in (pygame.K_UP, pygame.K_w):
                    selected = (selected - 1) % len(options)
                elif k in (pygame.K_DOWN, pygame.K_s):
                    selected = (selected + 1) % len(options)
                elif k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                    return options[selected]
                elif k == pygame.K_ESCAPE:
                    return options[0]
            body = list(lines) + [""] + [(f"> {o} <" if i == selected else o, menu_colours(i == selected)[0])
                                         for i, o in enumerate(options)]
            self.text_screen(title, body, "UP/DOWN choose    ENTER select")
            if screenshot:
                self.screenshot(screenshot)
            self.pull_data()
            time.sleep(0.015)

    # ------------------------------------------------------------------ main menu
    def record_key(self, settings=None):
        s = settings or self.settings
        return f"{s['size']}x{s['size']}-{s['difficulty']}-fog{int(s['fog'])}-guide{int(s['guide'])}"

    def main_menu(self):
        """Returns 'play', 'calibrate' or 'quit'."""
        s = self.settings
        items = ["Play", "Maze size", "Difficulty", "Fog of war", "Guide box", "Speed", "Calibration",
                 "Calibrate now", "Quit"]
        selected = 0
        if self.args.autostart:
            return "play"
        self.look_at(None)
        self.take_keys()

        def cycle(options, current, step):
            return options[(options.index(current) + step) % len(options)]

        while True:
            self.poll()
            for k in self.take_keys():
                item = items[selected]
                if k in (pygame.K_UP, pygame.K_w):
                    selected = (selected - 1) % len(items)
                elif k in (pygame.K_DOWN, pygame.K_s):
                    selected = (selected + 1) % len(items)
                elif k in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_a, pygame.K_d):
                    step = -1 if k in (pygame.K_LEFT, pygame.K_a) else 1
                    if item == "Maze size":
                        s["size"] = cycle(MAZE_SIZES, s["size"], step)
                    elif item == "Difficulty":
                        s["difficulty"] = cycle(DIFFICULTIES, s["difficulty"], step)
                    elif item == "Fog of war":
                        s["fog"] = not s["fog"]
                    elif item == "Guide box":
                        s["guide"] = not s["guide"]
                    elif item == "Speed":
                        s["speed"] = cycle(SPEED_NAMES, s["speed"], step)
                    elif item == "Calibration" and self.saved_profile:
                        s["calibration"] = "saved" if s["calibration"] == "new" else "new"
                elif k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                    if item == "Play":
                        return "play"
                    if item == "Calibrate now":
                        return "calibrate"
                    if item == "Quit":
                        return "quit"
                elif k == pygame.K_ESCAPE:
                    return "quit"
            self.draw_menu(items, selected)
            self.screenshot("menu")
            self.pull_data()
            time.sleep(0.015)

    def draw_menu(self, items, selected):
        scr, s = self.screen, self.settings
        if self.levels is not None:
            calibration = f"done ({self.calibration_source})"
        elif s["calibration"] == "saved" and self.saved_profile:
            calibration = f"use saved profile from {self.saved_profile['saved'][:16].replace('T', ' ')}"
        else:
            calibration = "new: runs before the first maze"
        values = {"Maze size": f"{s['size']} x {s['size']}",
                  "Difficulty": f"{s['difficulty']}  ({DIFFICULTY_TEXT[s['difficulty']]})",
                  "Fog of war": "On (only nearby corridors visible)" if s["fog"] else "Off",
                  "Guide box": "On (red box shows the next step)" if s["guide"] else "Off",
                  "Speed": f"{s['speed']}  ({SPEED_PRESETS[s['speed']].description})",
                  "Calibration": calibration}
        surf = scr.surface
        surf.fill(BLACK)
        img = scr.big.render("SSVEP Maze", True, WHITE)
        surf.blit(img, img.get_rect(midtop=(scr.w // 2, int(0.06 * scr.h))))
        x_label, x_value, y = int(0.25 * scr.w), int(0.42 * scr.w), int(0.22 * scr.h)
        for i, item in enumerate(items):
            label_colour, value_colour = menu_colours(i == selected)
            surf.blit(scr.font.render(("> " if i == selected else "  ") + item, True, label_colour), (x_label, y))
            if item in values:
                value = values[item]
                changeable = item != "Calibration" or (self.saved_profile and self.levels is None)
                if i == selected and changeable:
                    value = f"< {value} >"
                surf.blit(scr.small.render(value, True, value_colour), (x_value, y + 4))
            y += int(scr.font.get_linesize() * 1.35)
        record = self.records.get(self.record_key())
        y += int(0.02 * scr.h)
        if record:
            text = (f"Best for this setting: {format_time(record['best_time_s'])}   best stars {record['best_stars']}/3"
                    f"   completed {record['completed']} time(s)")
        else:
            text = "No record yet for this setting"
        img = scr.small.render(text, True, GOLD if record else DIM_GREY)
        surf.blit(img, img.get_rect(midtop=(scr.w // 2, y)))
        img = scr.small.render("UP/DOWN choose    LEFT/RIGHT change    ENTER select    ESC quit", True, DIM_GREY)
        surf.blit(img, img.get_rect(midbottom=(scr.w // 2, int(0.95 * scr.h))))
        pygame.display.flip()

    # ------------------------------------------------------------------ calibration
    def _load_saved_profile(self):
        profile = load_json(self.profile_path, None)
        if not profile or profile.get("freqs_hz") != self.args.freqs:
            return None
        if "anchors" not in profile:      # saved before Turbo existed: only the 1.25 s window was measured
            profile["anchors"] = {"1.25": {"c": profile["centre_median"], "t": profile["target_median"]}}
        return profile

    def ensure_calibrated(self):
        """Use this session's calibration, the saved profile, or run a new calibration."""
        if self.levels is not None:
            return True
        if self.settings["calibration"] == "saved" and self.saved_profile:
            from .decision import ReferenceLevels
            p = self.saved_profile
            self.levels = {float(w): ReferenceLevels(v["c"], v["t"]) for w, v in p["anchors"].items()}
            self.calibration_source = "saved " + p["saved"][:16].replace("T", " ")
            self.meta["calibration"] = {"reused_profile": self.profile_path, **p}
            return True
        return Calibration(self).run()

    # ------------------------------------------------------------------ the session
    def run(self):
        aborted = False
        try:
            self.board.start_stream(450000)
            time.sleep(1.0)
            self.board.get_board_data()                   # discard the first second (filters settle)
            if self.show_start_screen() == pygame.K_ESCAPE:
                raise QuitGame
            while True:
                choice = self.main_menu()
                if choice == "quit":
                    break
                if choice == "calibrate":
                    self.levels = None
                    self.settings["calibration"] = "new"
                    Calibration(self).run()
                    continue
                if not self.ensure_calibrated():
                    continue
                if self.play_mazes() == "quit":
                    break
        except QuitGame:
            aborted = True
        finally:
            matched = self.recorder.close()
            self.recording_closed = True
            try:
                self.board.stop_stream()
            except Exception:
                pass
            self.board.release_session()
            self.decisions_file.close()
        lines = self.summarize(matched, aborted)
        print("\n".join(lines))
        if not self.args.autostart:
            try:
                self.wait_for_key(lambda: self.text_screen(["Session saved"], lines[:18]), (pygame.K_SPACE, pygame.K_ESCAPE))
            except QuitGame:
                pass
        pygame.quit()

    def show_start_screen(self):
        channels_ok = len(self.meta["ssvep_channels"]) >= 3
        lines = PHOTOSENSITIVITY_WARNING + [
            "",
            "Look at a flickering button to move that way; look at the maze to stay still.",
            (f"Inputs +1..+{len(self.recorder.names)}: {', '.join(self.recorder.names)}    "
             f"classifying on: {', '.join(self.meta['ssvep_channels'])}", GOOD_GREEN if channels_ok else BAD_RED),
            f"Monitor refresh {self.screen.refresh_hz:.1f} Hz" + ("" if self.screen.vsync else "  (VSYNC NOT WORKING)")]
        return self.wait_for_key(lambda: self.text_screen(["SSVEP Maze"], lines, "SPACE continue    ESC quit"),
                                 (pygame.K_SPACE, pygame.K_ESCAPE), pygame.K_SPACE)

    def play_mazes(self):
        """Play mazes until the player goes back to the menu ('menu') or quits ('quit')."""
        maze = None
        while True:
            if maze is None:
                maze = Maze(self.settings["size"], self.settings["difficulty"], self.rng.randrange(10 ** 6))
            outcome, _ = Level(self, maze).play()
            if self.args.autostart and self.args.levels and self.level_number >= self.args.levels:
                outcome = "quit"
            if outcome == "restart":
                maze = Maze(maze.n, maze.difficulty, maze.seed)
            elif outcome == "new":
                maze = None
            else:
                return outcome

    # ------------------------------------------------------------------ saving
    def save_records(self, settings, stats):
        """Update personal bests for this combination of settings. Returns True for a new best time."""
        key = self.record_key(settings)
        record = self.records.get(key)
        new_best = record is None or stats["time_s"] < record["best_time_s"]
        if record is None:
            record = {"best_time_s": stats["time_s"], "best_stars": stats["stars"], "completed": 0}
        record["completed"] += 1
        record["best_stars"] = max(record["best_stars"], stats["stars"])
        if new_best:
            record["best_time_s"] = round(stats["time_s"], 2)
            record["best_date"] = datetime.now().isoformat(timespec="seconds")
            record["best_session"] = self.folder
        self.records[key] = record
        with open(self.records_path, "w") as f:
            json.dump(self.records, f, indent=2)
        return new_best

    def log_level(self, stats):
        """Store a finished or abandoned level in meta.json and the cross-day profile history."""
        decision_times = stats.pop("decision_times")
        stats["median_decision_s"] = float(np.median(decision_times)) if decision_times else None
        stats["bci_decisions"] = stats["decisions"] - stats["keyboard_moves"]
        self.level_log.append(stats)
        self.meta["levels"] = self.level_log
        self.write_meta()
        calibration = self.meta.get("calibration", {})
        keep = ("level", "size", "difficulty", "fog", "guide", "speed", "A", "window_s", "dead_s", "result", "time_s",
                "decisions", "bci_decisions", "moves", "optimal_moves", "bumps", "toward_goal", "guided",
                "guided_correct", "flagged", "median_decision_s")
        self.history({"type": "level", "calibration_source": self.calibration_source,
                      "calibration_min_separation": calibration.get("min_separation_any_window"),
                      **{k: stats.get(k) for k in keep}})

    def history(self, record):
        """Append one line to profile_history.jsonl: every calibration and level, across all days."""
        try:
            with open(os.path.join(self.subject_dir, "profile_history.jsonl"), "a") as f:
                f.write(json.dumps({"logged": datetime.now().isoformat(timespec="seconds"),
                                    "session": self.folder, **record}, default=float) + "\n")
        except OSError as e:
            print("could not write profile history:", e)

    def write_meta(self):
        self.meta.update({"n_samples": self.recorder.n, "dropped_frames": self.dropped_frames})
        with open(os.path.join(self.folder, "meta.json"), "w") as f:
            json.dump(self.meta, f, indent=2, default=float)

    def summarize(self, matched, aborted):
        """The session summary printed at the end and saved to summary.txt."""
        levels = self.level_log
        lines = [f"Session: {self.folder}",
                 f"markers in recording: {matched}/{len(self.recorder.events)}, dropped frames: {self.dropped_frames}, "
                 f"refresh {self.screen.refresh_hz:.1f} Hz", ""]
        if self.calibration_report:
            lines.append("Calibration: separation (looking - not looking) "
                         + ", ".join(f"{d} {x:.2f}" for d, x in zip(DIRECTIONS, self.calibration_report["separation"]))
                         + f"; recommended {self.calibration_report['recommended_speed']}")
        elif self.calibration_source:
            lines.append(f"Calibration: {self.calibration_source}")
        lines.append("")
        lines.append("lvl size diff    fog guide speed    result     time  moves/best bumps toward  s/decision stars flagged")
        for s in levels:
            lines.append(f"{s['level']:>3} {s['size']:>2}x{s['size']:<2} {s['difficulty']:<7} {int(s['fog']):>3} "
                         f"{int(s['guide']):>5} {s['speed']:<8} {s['result']:<9} {format_time(s['time_s']):>6} "
                         f"{s['moves']:>5}/{s['optimal_moves']:<4} {s['bumps']:>5} {s['toward_goal']:>6} "
                         f"{s['median_decision_s'] or float('nan'):>10.2f} {s.get('stars', '-'):>5} {s['flagged']:>7}")
        n_decisions = sum(s["bci_decisions"] for s in levels)
        play_time = sum(s["time_s"] for s in levels)
        if n_decisions:
            moves = sum(s["moves"] for s in levels)
            toward = sum(s["toward_goal"] for s in levels)
            lines += ["", f"{n_decisions} headset decisions in {play_time:.0f} s of play "
                          f"({60 * n_decisions / max(play_time, 1e-9):.1f} per minute); "
                          f"{toward}/{moves} moves went toward the goal, {sum(s['bumps'] for s in levels)} wall bumps, "
                          f"{sum(s['flagged'] for s in levels)} flagged as unintended"]
            guided = sum(s["guided"] for s in levels)
            if guided:
                correct = sum(s["guided_correct"] for s in levels)
                seconds_each = sum(s["time_s"] for s in levels if s["guide"]) / guided
                lines.append(f"Guided: {correct}/{guided} = {100 * correct / guided:.1f}% correct, ITR ~"
                             f"{information_transfer_rate(4, correct / guided, seconds_each):.1f} "
                             f"bits/min at {seconds_each:.1f} s per decision")
        self.meta.update({"completed": not aborted, "events_logged": len(self.recorder.events),
                          "events_found_in_recording": matched})
        self.write_meta()
        with open(os.path.join(self.folder, "summary.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
        return lines


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default
