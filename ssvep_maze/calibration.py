"""
Calibration (~70 s): measures what each button's score looks like when you are and aren't looking at it.

Sequence:
  1. Two blocks of looking at the centre of the maze ("not looking" at any button).
  2. Each button cued 3 times, 3 s each, in a shuffled order each round.
Before every block the flicker stops for a moment so you can blink, and the red box already shows
the next target, so your eyes are in place when the flicker starts.

Afterwards it replays every speed preset over what it just recorded, recommends a speed, flags
weak buttons or single bad cues, and saves the result so it can be reused.
"""
import json
import time
from datetime import datetime

import pygame

from . import decision
from .maze import Maze
from .settings import (BAD_RED, DIM_GREY, DIRECTIONS, GOLD, MARKER, SCORE_INTERVAL_S,
                       SPEED_NAMES, WHITE, WINDOW_LENGTHS_S)


class Calibration:
    def __init__(self, game):
        self.game, self.args = game, game.args

    def steps(self):
        """[(target, flicker seconds, break seconds)]: two maze-gaze blocks, then the button cues."""
        a = self.args
        half = a.calib_centre / 2
        steps = [("maze", half, 2.0), ("maze", half, 2.0)]
        for _ in range(a.calib_rounds):
            order = DIRECTIONS[:]
            self.game.rng.shuffle(order)
            steps += [(d, a.calib_cue, a.calib_break) for d in order]
        return steps

    def total_seconds(self):
        a = self.args
        return 2.0 + a.calib_centre + 4.0 + a.calib_rounds * 4 * (a.calib_cue + a.calib_break)

    def run(self):
        """Returns True if calibration finished and was accepted."""
        g = self.game
        intro = [f"About {int(round(self.total_seconds()))} s, with a short break to blink before every part.",
                 "When the flicker stops: blink, then look where the screen says",
                 "(the centre of the maze, or the button with the red box). Keep looking until the flicker stops.",
                 "Keep your jaw relaxed and sit still."]
        if g.wait_for_key(lambda: g.text_screen(["Calibration"], intro, "SPACE start    ESC back to menu"),
                          (pygame.K_SPACE, pygame.K_ESCAPE), pygame.K_SPACE) == pygame.K_ESCAPE:
            return False

        # A maze to look at (not played), so the screen looks exactly like it will during play.
        demo = Maze(g.settings["size"], g.settings["difficulty"], g.rng.randrange(10 ** 6))
        g.view.render_maze(demo, demo.start, [demo.start])
        g.view.set_hud(["Calibration"])
        g.restart_frame_pacing()
        g.view.set_banner("Get ready: look at the maze")
        g.look_at(None)
        warm_up_until = time.perf_counter() + 2.0
        while time.perf_counter() < warm_up_until:
            g.frame()

        self.steps_used = self.steps()
        ticks = {w: [] for w in WINDOW_LENGTHS_S}     # every score computed, per window length
        rejected = total = 0
        for k, (target, flicker_s, break_s) in enumerate(self.steps_used):
            on_button = target != "maze"
            # Blink break: flicker off, next target already shown.
            g.view.set_banner("Blink now, then look at the red box" if on_button
                              else "Blink now, then look at the centre of the maze")
            g.mark(MARKER["calibration_break"], "calib_break", target=target)
            g.look_at(None)
            break_until = time.perf_counter() + break_s
            while time.perf_counter() < break_until:
                g.frame(cue=target if on_button else None, flickering=False)
                g.screenshot("calibration_break")
            # Flicker on: record scores.
            g.view.set_banner(None if on_button else "Keep looking at the centre of the maze")
            if on_button:
                g.mark(MARKER["calibration_cue"] + DIRECTIONS.index(target), "calib", target=target)
            else:
                g.mark(MARKER["calibration_maze"], "calib", target="maze")
            g.look_at(target if on_button else None)
            started = g.now()
            next_score = started + SCORE_INTERVAL_S
            while True:
                now = g.frame(cue=target if on_button else None)
                if on_button:
                    g.screenshot("calibration")
                if now - started >= flicker_s:
                    break
                if now >= next_score:
                    next_score += SCORE_INTERVAL_S
                    total += 1
                    for w, scorer in g.scorers.items():
                        scores = scorer.scores(g.recorder.buf, self.args.reject_uv)
                        if scores is not None:
                            ticks[w].append((k, target, now - started, scores))
                        elif w == max(WINDOW_LENGTHS_S):
                            rejected += 1
        g.view.set_banner(None)
        g.look_at(None)
        return self.finish(ticks, rejected / max(1, total))

    def finish(self, ticks, rejected_fraction):
        """Compute the reference levels, check every preset, report, and save."""
        g, a = self.game, self.args
        levels = decision.reference_levels_from_calibration(ticks)
        if levels is None:
            g.wait_for_key(lambda: g.text_screen(["Calibration failed"],
                                                 ["No usable windows while looking at the maze.",
                                                  "Check electrode contact (too many blinks or movement artifacts)."],
                                                 "SPACE back to menu"), (pygame.K_SPACE, pygame.K_ESCAPE))
            return False
        ref = levels[1.25]
        check = decision.check_presets_on_calibration(ticks, levels, a.horizon, a.floor)
        weak = decision.weak_buttons(levels)
        weakest_any_window = min(levels[w].separation.min() for w in WINDOW_LENGTHS_S)
        recommended = "Normal" if (decision.preset_passed(check["Normal"]) and not weak) else "Careful"
        cue_quality, low_cues = decision.per_cue_quality(ticks[1.25], self.steps_used, levels)

        lines = []
        for i, d in enumerate(DIRECTIONS):
            note = "   <- weak: adjust the electrodes or rest your eyes" if d in weak else ""
            lines.append((f"{d:>5} ({g.freq_of[d]:g} Hz): {ref.not_looking[i]:.2f} not looking -> "
                          f"{ref.looking[i]:.2f} looking{note}", BAD_RED if note else WHITE))
        lines.append("")
        n_cues = sum(1 for target, _, _ in self.steps_used if target != "maze")
        lines.append(f"Replaying this calibration with each speed ({n_cues} cues, and the "
                     f"{a.calib_centre:g} s looking at the maze):")
        for name in SPEED_NAMES:
            r = check[name]
            lines.append(f"  {name:>7}: {r['correct']}/{r['cues']} cues right, {r['wrong']} wrong, "
                         f"{r['undecided']} undecided, median {r['median_s']:.1f} s; "
                         f"{r['maze_false']} false move(s) while looking at the maze")
        lines.append("")
        why = {"Normal": "  (Turbo also passed this check: your call)" if decision.preset_passed(check["Turbo"]) else "",
               "Careful": "  (the signal looks weaker than in your best session)"}[recommended]
        lines.append((f"Recommended speed: {recommended}{why}", GOLD))
        if rejected_fraction > 0.2:
            lines.append((f"{100 * rejected_fraction:.0f}% of windows were rejected as artifacts: sit still, "
                          "relax your jaw", BAD_RED))
        lines.append(("Replaying the same data it was fitted on is optimistic; treat it as a sanity check.", DIM_GREY))
        if low_cues:
            lines.append((f"{len(low_cues)} cue(s) came out well below that button's usual level (a blink, a glance "
                          f"away, or contact): {', '.join(low_cues)}", DIM_GREY))

        g.levels, g.calibration_source = levels, "this session"
        g.settings["speed"] = recommended
        g.calibration_report = {
            "centre_median": ref.not_looking.tolist(), "target_median": ref.looking.tolist(),
            "separation": ref.separation.tolist(),
            "anchors": {f"{w:g}": {"c": levels[w].not_looking.tolist(), "t": levels[w].looking.tolist()}
                        for w in WINDOW_LENGTHS_S},
            "min_separation_any_window": float(weakest_any_window), "per_cue": cue_quality,
            "design": {"rounds": a.calib_rounds, "cue_s": a.calib_cue, "break_s": a.calib_break,
                       "centre_s": a.calib_centre},
            "weak_buttons": weak, "reject_fraction": rejected_fraction, "check": check,
            "recommended_speed": recommended, "n_windows": len(ticks[1.25])}
        g.meta["calibration"] = g.calibration_report
        profile = {"saved": datetime.now().isoformat(timespec="seconds"), "session": g.folder,
                   "freqs_hz": a.freqs, "windows_s": WINDOW_LENGTHS_S, "channels": g.recorder.names,
                   **g.calibration_report}
        with open(g.profile_path, "w") as f:
            json.dump(profile, f, indent=2)
        g.saved_profile = profile
        g.history({"type": "calibration", "layout": g.meta["layout"], **profile})
        g.write_meta()
        print("Calibration\n" + "\n".join(x if isinstance(x, str) else x[0] for x in lines))

        def show():
            g.text_screen(["Calibration done"], lines,
                          "SPACE continue (speed set to the recommendation; change it in the menu)")
            g.screenshot("calibration_report")
        g.wait_for_key(show, (pygame.K_SPACE, pygame.K_RETURN, pygame.K_ESCAPE))
        return True
