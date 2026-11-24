#!/usr/bin/env python3
"""
Replay a recorded game session offline and check that the decision code reproduces the live moves.

It filters raw.csv exactly as the game does live (causal 6-45 Hz band-pass + 60 Hz notch), scores a
window every 0.1 s, rescales with the session's calibration, and runs the same EvidenceAccumulator
from each move to the next. Useful for checking a change to the decision rule against real data.

    python tools/replay_session.py data/sub-S01/game-20261123-141841
    python tools/replay_session.py <session> --floor 0.45            # try a different setting

Limits: uses the calibration stored in meta.json (the last one of the session), so mazes played on an
earlier calibration in the same session may differ slightly.
"""
import argparse
import csv
import json
import os
import sys

import numpy as np
from scipy import signal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ssvep_maze.decision import EvidenceAccumulator, ReferenceLevels      # noqa: E402
from ssvep_maze.settings import DIRECTIONS, SPEED_PRESETS, VISUAL_CHANNEL_NAMES   # noqa: E402
from ssvep_maze.signal_processing import FlickerScorer, make_live_filter   # noqa: E402


def load_session(folder):
    meta = json.load(open(os.path.join(folder, "meta.json")))
    events = list(csv.DictReader(open(os.path.join(folder, "events.csv"))))
    decisions = list(csv.DictReader(open(os.path.join(folder, "decisions.csv"))))
    header = open(os.path.join(folder, "raw.csv")).readline().strip().split(",")
    channels = [c for c in meta["channel_names"] if c in VISUAL_CHANNEL_NAMES]
    columns = [header.index(f"{c}_uV") for c in channels]
    raw = np.loadtxt(os.path.join(folder, "raw.csv"), delimiter=",", skiprows=1, usecols=columns)
    return meta, events, decisions, raw


def reference_levels(meta):
    cal = meta.get("calibration", {})
    if "anchors" in cal:
        return {float(w): ReferenceLevels(v["c"], v["t"]) for w, v in cal["anchors"].items()}
    return {1.25: ReferenceLevels(cal["centre_median"], cal["target_median"])}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("session")
    p.add_argument("--floor", type=float, default=None, help="override the minimum average score")
    p.add_argument("--horizon", type=float, default=None, help="override the evidence memory (s)")
    a = p.parse_args()

    meta, events, decisions, raw = load_session(a.session)
    fs = meta["sampling_rate_hz"]
    filtered = signal.sosfilt(make_live_filter(fs), raw, axis=0)         # causal, same as live
    levels = reference_levels(meta)
    floor = a.floor if a.floor is not None else meta["controller"]["floor"]
    horizon = a.horizon if a.horizon is not None else meta["controller"]["horizon_s"]
    reject = meta["controller"]["reject_uv"]
    scorers = {w: FlickerScorer(meta["freqs_hz"], fs, w) for w in levels}

    feedback = [e for e in events if e["event"] == "feedback" and e["sample"]]
    starts = sorted(int(e["sample"]) for e in events if e["event"] in ("feedback", "level_start", "resume") and e["sample"])
    if len(feedback) != len(decisions):
        sys.exit("events.csv and decisions.csv don't line up; is this a maze_game session?")

    same_dir = same_time = compared = 0
    for ev, dec in zip(feedback, decisions):
        if dec["source"] != "bci":
            continue
        end = int(ev["sample"])
        start = max(s for s in starts if s < end)
        preset = SPEED_PRESETS[dec["speed"]]
        if preset.window_s not in levels:
            continue
        ref, scorer = levels[preset.window_s], scorers[preset.window_s]
        acc = EvidenceAccumulator(float(dec["A"]), preset.pause_s, horizon, floor)
        acc.reset(0.0)
        choice = None
        for i in range(1, int((end - start) / fs * 10) + 4):               # until just after the live move
            t = i / 10
            buf = filtered[:start + int(round(t * fs))]
            scores = scorer.scores(buf, reject)
            d = acc.update(t, None if scores is None else ref.normalise(scores))
            if d is not None:
                choice = (DIRECTIONS[d], t)
                break
        compared += 1
        if choice and choice[0] == dec["predicted"]:
            same_dir += 1
            same_time += abs(choice[1] - float(dec["decision_time_s"])) <= 0.25
    print(f"{compared} headset moves replayed: {same_dir} same direction, {same_time} also within 0.25 s of the live time")
    print(f"(floor {floor}, evidence memory {horizon} s)")


if __name__ == "__main__":
    main()
