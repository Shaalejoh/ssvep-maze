"""
Deciding when to move, and what calibration measures.

Raw CCA scores aren't comparable between buttons: some frequencies come through more strongly
than others, and the background level depends on the window length. So calibration measures,
for each button, two reference levels:

    not_looking: its typical score while you look at the centre of the maze
    looking:     its typical score while you look at that button

and every live score is rescaled so 0 = "not looking" and 1 = "looking". After that, the
EvidenceAccumulator adds the rescaled scores up over time and decides when one button has
clearly won.

No pygame or hardware code here, so it can be tested and replayed offline.
"""
import collections

import numpy as np

from .settings import (DIRECTIONS, EVIDENCE_MEMORY_S, LOW_CUE_FRACTION, MIN_AVERAGE_SCORE, SCORE_CLIP,
                       SCORE_INTERVAL_S, SETTLE_S, SPEED_PRESETS, WEAK_BUTTON_SEPARATION)


class ReferenceLevels:
    """Per-button 'not looking' and 'looking' scores for one window length."""

    def __init__(self, not_looking, looking):
        self.not_looking = np.asarray(not_looking, dtype=float)
        # Guard against a button whose looking level isn't above its background (would divide by ~0).
        self.looking = np.maximum(np.asarray(looking, dtype=float), self.not_looking + 1e-3)

    def normalise(self, raw_scores):
        """0 = typical when not looking, 1 = typical when looking."""
        return (raw_scores - self.not_looking) / (self.looking - self.not_looking)

    @property
    def separation(self):
        return self.looking - self.not_looking


class EvidenceAccumulator:
    """Adds up normalised scores and says when one button has clearly won.

    Rules, applied every scoring tick (0.1 s):
      1. For `pause_s` after a move, ignore everything: your eyes are still travelling.
      2. Add each button's (clipped) normalised score to its running total. Only the last
         `memory_s` seconds count, so stale evidence from studying the maze fades away.
      3. Move when the leader's total beats the runner-up's by `margin` AND the leader's average
         is at least `min_average`. The first rule makes it decisive; the second stops small
         drifts (for example flicker leaking in from the side of your vision) adding up to a move.
    """

    def __init__(self, margin, pause_s=1.25, memory_s=EVIDENCE_MEMORY_S, min_average=MIN_AVERAGE_SCORE,
                 tick_s=SCORE_INTERVAL_S):
        self.margin, self.pause_s, self.min_average = margin, pause_s, min_average
        self.memory_ticks = max(1, int(round(memory_s / tick_s)))
        self.reset(0.0)

    def reset(self, now, pause_s=None):
        """Start fresh (after a move, at the start of a maze, after a pause)."""
        self.started_at = now
        self.current_pause = self.pause_s if pause_s is None else max(self.pause_s, pause_s)
        self.recent = collections.deque()          # the clipped scores still in memory, oldest first
        self.totals = np.zeros(len(DIRECTIONS))

    def update(self, now, normalised_scores):
        """Feed one tick. normalised_scores is None when the window was rejected as an artifact.

        Returns the index of the chosen direction, or None.
        """
        if now - self.started_at < self.current_pause - 1e-9 or normalised_scores is None:
            return None
        clipped = np.clip(normalised_scores, *SCORE_CLIP)
        self.recent.append(clipped)
        self.totals += clipped
        if len(self.recent) > self.memory_ticks:
            self.totals -= self.recent.popleft()
        leader, runner_up = self._top_two()
        clear_lead = self.totals[leader] - self.totals[runner_up] >= self.margin
        strong_enough = self.totals[leader] / len(self.recent) >= self.min_average
        return leader if (clear_lead and strong_enough) else None

    def _top_two(self):
        order = np.argsort(self.totals)
        return int(order[-1]), int(order[-2])

    def progress(self):
        """For the on-screen bars: how far the leader is towards a move (0-1), and whether it is strong enough."""
        fraction, strong = np.zeros(len(DIRECTIONS)), np.zeros(len(DIRECTIONS), bool)
        if self.recent:
            leader, runner_up = self._top_two()
            fraction[leader] = min(1.0, max(0.0, (self.totals[leader] - self.totals[runner_up]) / self.margin))
            strong[leader] = self.totals[leader] / len(self.recent) >= self.min_average
        return fraction, strong

    def stats(self):
        """(leader total, lead over runner-up, leader average), logged with every decision."""
        if not self.recent:
            return 0.0, 0.0, 0.0
        ordered = np.sort(self.totals)
        return float(ordered[-1]), float(ordered[-1] - ordered[-2]), float(ordered[-1] / len(self.recent))


# ----------------------------------------------------------------------------- calibration maths
def reference_levels_from_calibration(ticks):
    """Median score while looking at the maze, and at each button, per window length.

    ticks[window_s] = [(segment_number, target, seconds_since_flicker_on, raw_scores), ...]
    Only windows that start after the response has settled are used.
    Returns {window_s: ReferenceLevels}, or None if there were no usable maze-gaze windows.
    """
    levels = {}
    for window_s, rows in ticks.items():
        settle = SETTLE_S + window_s
        maze = np.array([s for _, target, t, s in rows if target == "maze" and t >= settle])
        if len(maze) == 0:
            return None
        not_looking = np.median(maze, axis=0)
        looking = []
        for i, d in enumerate(DIRECTIONS):
            own = [s[i] for _, target, t, s in rows if target == d and t >= settle]
            looking.append(np.median(own) if own else not_looking[i] + 1e-3)
        levels[window_s] = ReferenceLevels(not_looking, looking)
    return levels


def check_presets_on_calibration(ticks, levels, memory_s, min_average):
    """Replay every speed preset over the calibration recording.

    Each button cue is treated like a move: does the preset pick the cued button, how fast, and
    does it ever 'move' during the look-at-the-maze blocks? Optimistic (it's the same data the
    reference levels came from), so it's a sanity check rather than a prediction.
    """
    results = {}
    for name, preset in SPEED_PRESETS.items():
        ref = levels[preset.window_s]
        segments = collections.OrderedDict()
        for k, target, t, raw in ticks[preset.window_s]:
            segments.setdefault(k, (target, []))[1].append((t, ref.normalise(raw)))
        r = {"cues": 0, "correct": 0, "wrong": 0, "undecided": 0, "times": [], "maze_false": 0, "maze_s": 0.0}
        for target, rows in segments.values():
            acc = EvidenceAccumulator(preset.margin, preset.pause_s, memory_s, min_average)
            if target == "maze":
                rows = [x for x in rows if x[0] >= 1.0]
                if not rows:
                    continue
                acc.reset(rows[0][0])
                for t, scores in rows:
                    if acc.update(t, scores) is not None:
                        r["maze_false"] += 1
                        acc.reset(t)
                r["maze_s"] += rows[-1][0] - rows[0][0]
                continue
            r["cues"] += 1
            acc.reset(0.0)
            for t, scores in rows:
                choice = acc.update(t, scores)
                if choice is not None:
                    r["correct" if DIRECTIONS[choice] == target else "wrong"] += 1
                    r["times"].append(t)
                    break
            else:
                r["undecided"] += 1
        r["median_s"] = float(np.median(r["times"])) if r["times"] else float("nan")
        del r["times"]
        results[name] = r
    return results


def preset_passed(check):
    """No wrong picks, no false moves while looking at the maze, at most one undecided cue."""
    return check["wrong"] == 0 and check["maze_false"] == 0 and check["undecided"] <= 1


def weak_buttons(levels):
    ref = levels[1.25]
    return [d for i, d in enumerate(DIRECTIONS) if ref.separation[i] < WEAK_BUTTON_SEPARATION]


def per_cue_quality(ticks_125, steps, levels):
    """How each individual cue compared with its button's usual level (spots a blink or glance away)."""
    ref = levels[1.25]
    quality = []
    for k, (target, _, _) in enumerate(steps):
        if target == "maze":
            continue
        i = DIRECTIONS.index(target)
        own = [s[i] for kk, _, t, s in ticks_125 if kk == k and t >= SETTLE_S + 1.25]
        median = float(np.median(own)) if own else None
        quality.append({"button": target, "own_score_median": median,
                        "normalised": float((median - ref.not_looking[i]) / ref.separation[i]) if own else None})
    low = [q["button"] for q in quality if q["normalised"] is not None and q["normalised"] < LOW_CUE_FRACTION]
    return quality, low
