#!/usr/bin/env python3
"""
Four-target SSVEP recording for the maze (OpenBCI Ganglion + BrainFlow + pygame).

Four squares (up, right, down, left) flicker AT THE SAME TIME, each at its own frequency, like
the maze's direction buttons. A red border shows which one to look at. There is also a
"centre" condition: look at the cross in the middle while all four flicker. That is the
"don't move" state the maze needs to recognise.

WARNING: flickering light can trigger seizures in people with photosensitive epilepsy.

Needs ssvep_test.py and analyze_ssvep.py in the same folder. Close the OpenBCI GUI first.

    py ssvep_4target.py --channels O1,Oz,O2,Cz
    py ssvep_4target.py --board synthetic --windowed          (dry run, no headset)

Each trial: cue 1.5 s (squares still, red border on the target, look at it)
            -> flicker 4 s (keep looking at the cross on the target)
            -> black 1.5 s (blink now).
Default 10 trials per condition (4 directions + centre) = 50 trials, ~6 min, with a break
every 25 trials. Analysis runs automatically at the end.

Marker codes: 1..4 = flicker onset, attended target = freqs[0..3] (up, right, down, left)
              50 = flicker onset, centre (no target), 30 = cue onset, 20 = flicker offset
"""
import argparse
import json
import math
import os
import random
import time
from datetime import datetime

import pygame

from ssvep_test import (BG, FIX, REST_CODE, STIM_OFF_CODE, WARNING, Abort, Display, Recorder,
                        blank, open_board, pump, wait_key)

CUE_CODE = 30
DIRS = ["up", "right", "down", "left"]
CUE_COLOR = (230, 40, 40)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--freqs", default="9,11,13,15", help="Hz for up,right,down,left")
    p.add_argument("--subject", default="S01")
    p.add_argument("--board", choices=["ganglion", "ganglion-native", "synthetic"], default="ganglion")
    p.add_argument("--serial-port", default=None)
    p.add_argument("--mac-address", default=None)
    p.add_argument("--firmware", choices=["2", "3", "auto"], default="auto")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--channels", default="O1,Oz,O2,Cz")
    p.add_argument("--trials", type=int, default=10, help="trials per condition")
    p.add_argument("--no-centre", action="store_true", help="leave out the look-at-centre condition")
    p.add_argument("--cue", type=float, default=1.5)
    p.add_argument("--stim", type=float, default=4.0)
    p.add_argument("--iti", type=float, default=1.5)
    p.add_argument("--block", type=int, default=25, help="break after this many trials")
    p.add_argument("--size", type=float, default=0.22, help="square size, fraction of screen height")
    p.add_argument("--spread", type=float, default=0.30, help="centre-to-square distance, fraction of height")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--out", default="data")
    p.add_argument("--windowed", action="store_true")
    p.add_argument("--autostart", action="store_true")
    a = p.parse_args()
    a.freqs = [float(f) for f in a.freqs.split(",")]
    if len(a.freqs) != 4:
        p.error("--freqs needs exactly four values (up,right,down,left)")
    a.channels = [c.strip() for c in a.channels.split(",") if c.strip()]
    a.size_frac = a.size
    return a


class Board4:
    """Four squares around the centre of the screen."""

    def __init__(self, disp, size_frac, spread_frac):
        self.d = disp
        cx, cy, h = disp.w // 2, disp.h // 2, disp.h
        side, off = int(h * size_frac), int(h * spread_frac)
        centres = {"up": (cx, cy - off), "right": (cx + off, cy), "down": (cx, cy + off), "left": (cx - off, cy)}
        self.rects = {}
        for k, c in centres.items():
            r = pygame.Rect(0, 0, side, side)
            r.center = c
            self.rects[k] = r
        self.centre = (cx, cy)
        self.border = max(4, h // 120)

    def cross(self, at):
        s, t = max(8, self.d.h // 90), max(2, self.d.h // 400)
        pygame.draw.line(self.d.surface, FIX, (at[0] - s, at[1]), (at[0] + s, at[1]), t)
        pygame.draw.line(self.d.surface, FIX, (at[0], at[1] - s), (at[0], at[1] + s), t)

    def draw(self, levels, target):
        """levels: dict dir -> 0..1; target: a dir or 'centre'. Returns flip time."""
        surf = self.d.surface
        surf.fill(BG)
        for k, r in self.rects.items():
            g = int(round(255 * min(1.0, max(0.0, levels[k]))))
            pygame.draw.rect(surf, (g, g, g), r)
        if target == "centre":
            self.cross(self.centre)
        else:
            r = self.rects[target]
            pygame.draw.rect(surf, CUE_COLOR, r.inflate(2 * self.border + 6, 2 * self.border + 6), self.border)
            self.cross(r.center)
        pygame.display.flip()
        return time.perf_counter()


def still(b4, target, seconds, on_first=None):
    end, first = time.perf_counter() + seconds, True
    while time.perf_counter() < end:
        pump()
        b4.draw({k: 0.5 for k in DIRS}, target)
        if first and on_first:
            on_first()
            first = False


def flicker4(b4, freqs, target, seconds, on_first=None):
    disp = b4.d
    n_frames = int(round(seconds * disp.refresh_hz))
    dropped, last, t0 = 0, None, time.perf_counter()
    for n in range(n_frames):
        pump()
        if not disp.vsync:
            wait = t0 + n * disp.frame_s - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
        ph = 2 * math.pi * n * disp.frame_s
        t = b4.draw({k: 0.5 * (1 + math.sin(f * ph)) for k, f in zip(DIRS, freqs)}, target)
        if n == 0 and on_first:
            on_first()
        if last is not None and t - last > 1.5 * disp.frame_s:
            dropped += int(round((t - last) / disp.frame_s)) - 1
        last = t
    return dropped


def main():
    a = parse_args()
    seed = a.seed if a.seed is not None else random.SystemRandom().randrange(1_000_000)
    rng = random.Random(seed)
    targets = DIRS + ([] if a.no_centre else ["centre"])
    order = [t for t in targets for _ in range(a.trials)]
    for _ in range(5000):
        rng.shuffle(order)
        if all(order[i] != order[i + 1] or order[i] != order[i + 2] for i in range(len(order) - 2)):
            break
    fmap = dict(zip(DIRS, a.freqs))
    cond = {d: f"{fmap[d]:g}" for d in DIRS}
    cond["centre"] = "rest"
    codes = {d: i + 1 for i, d in enumerate(DIRS)}
    codes["centre"] = REST_CODE

    disp = Display(a.windowed, 0.3)
    b4 = Board4(disp, a.size_frac, a.spread)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = os.path.join(a.out, f"sub-{a.subject}", f"ssvep4-{stamp}")
    os.makedirs(folder, exist_ok=True)
    board, board_id = open_board(a)
    rec = Recorder(board, board_id, a.channels, folder)
    meta = {"paradigm": "ssvep-4target", "subject": a.subject, "session": stamp, "board": a.board,
            "board_id": board_id, "sampling_rate_hz": rec.fs, "channel_names": rec.channel_names,
            "freqs_hz": a.freqs, "directions": dict(zip(DIRS, a.freqs)),
            "conditions": [cond[d] for d in targets], "rest_meaning": "look at centre cross, all four flickering",
            "marker_codes": {**{cond[d]: codes[d] for d in targets}, "cue": CUE_CODE, "stim_off": STIM_OFF_CODE},
            "timing_s": {"cue": a.cue, "stim": a.stim, "iti": a.iti}, "trials_per_condition": a.trials,
            "trial_order": order, "seed": seed, "refresh_hz": round(disp.refresh_hz, 3), "vsync": disp.vsync,
            "layout": {"size_frac_height": a.size, "spread_frac_height": a.spread},
            "raw_columns": rec.header, "completed": False}
    meta_path = os.path.join(folder, "meta.json")
    json.dump(meta, open(meta_path, "w"), indent=2)

    minutes = len(order) * (a.cue + a.stim + a.iti) / 60
    aborted, drops = False, []
    try:
        board.start_stream(450000)
        time.sleep(1.0)
        board.get_board_data()
        wait_key(disp, WARNING,
                 [f"{len(order)} trials, about {minutes:.0f} min, break every {a.block}.",
                  "A red border shows which square to look at. Look at the cross on it",
                  "and keep looking until the screen goes black. If the cross is in the",
                  "middle, look at the middle and ignore the squares.",
                  "Stay still, relax your jaw, blink only on the black screen.",
                  f"Monitor refresh {disp.refresh_hz:.1f} Hz" + ("" if disp.vsync else " (VSYNC NOT WORKING)"),
                  "", "SPACE = start    ESC = stop (data so far is kept)"], a.autostart)
        blank(disp, 1.5, during=rec.drain)
        for trial, tgt in enumerate(order, start=1):
            if trial > 1 and (trial - 1) % a.block == 0:
                rec.drain()
                wait_key(disp, [f"Break: {trial - 1} of {len(order)} done."],
                         ["Relax, blink, adjust if needed (don't touch the electrodes).",
                          "SPACE = continue"], a.autostart)
                blank(disp, 1.0, during=rec.drain)
            still(b4, tgt, a.cue, on_first=lambda t=trial, g=tgt: rec.mark(CUE_CODE, t, "cue", cond[g]))
            drops.append(flicker4(b4, a.freqs, tgt, a.stim,
                                  on_first=lambda t=trial, g=tgt: rec.mark(codes[g], t, "stim", cond[g])))
            disp.surface.fill(BG); pygame.display.flip()
            rec.mark(STIM_OFF_CODE, trial, "stim_off", cond[tgt])
            blank(disp, a.iti, during=rec.drain)
    except Abort:
        aborted = True
    finally:
        matched = rec.close()
        try:
            board.stop_stream()
        except Exception:
            pass
        board.release_session()

    meta.update({"completed": not aborted, "n_samples": rec.n_samples,
                 "duration_s": round(rec.n_samples / rec.fs, 1), "events_logged": len(rec.events),
                 "events_found_in_recording": matched, "dropped_frames_per_trial": drops})
    json.dump(meta, open(meta_path, "w"), indent=2)
    print(f"\n{'Stopped early' if aborted else 'Session complete'}: {folder}")
    print(f"  {rec.n_samples} samples, markers {matched}/{len(rec.events)}, "
          f"refresh {disp.refresh_hz:.1f} Hz, dropped frames {sum(drops)}")
    try:
        from analyze_ssvep import analyze
        summary = analyze(folder)
    except Exception as exc:
        summary = [f"Analysis failed: {exc}", "Run: py analyze_ssvep.py " + folder]
    print("\n".join(summary))
    if not a.autostart:
        try:
            wait_key(disp, ["Done."] + [l for l in summary if l.strip()][:14],
                     ["Results saved in " + folder, "Press SPACE or ESC to close."], False)
        except Abort:
            pass
    pygame.quit()


if __name__ == "__main__":
    try:
        main()
    except Abort:
        pygame.quit()
