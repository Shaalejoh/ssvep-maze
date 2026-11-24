import numpy as np

from ssvep_maze.decision import (EvidenceAccumulator, ReferenceLevels, check_presets_on_calibration,
                                 reference_levels_from_calibration)

UP, RIGHT = 0, 1


def feed(acc, scores_per_tick, start=0.1, tick=0.1):
    """Feed identical scores every tick; return (direction, time) of the first decision, or None."""
    t = start
    for _ in range(200):
        d = acc.update(t, np.array(scores_per_tick, dtype=float))
        if d is not None:
            return d, round(t, 2)
        t += tick
    return None


def test_moves_toward_the_button_being_looked_at():
    acc = EvidenceAccumulator(margin=3.0, pause_s=1.25)
    choice, when = feed(acc, [1.0, 0.0, 0.0, 0.0])
    assert choice == UP
    # Nothing counts during the 1.25 s pause; then a lead of 1.0 per tick needs 3 ticks.
    assert 1.25 <= when <= 1.6


def test_no_move_while_looking_at_the_maze():
    acc = EvidenceAccumulator(margin=3.0, pause_s=1.0)
    rng = np.random.default_rng(1)
    t = 0.1
    for _ in range(600):                      # a minute of background-level scores
        assert acc.update(t, rng.normal(0.0, 0.15, 4)) is None
        t += 0.1


def test_weak_but_consistent_lead_is_blocked_by_the_minimum_average():
    # Right leads every tick, but only reaches 0.3 of its 'looking' level: below the 0.4 minimum.
    acc = EvidenceAccumulator(margin=3.0, pause_s=1.0, min_average=0.4)
    assert feed(acc, [-0.1, 0.3, -0.1, -0.1]) is None
    acc = EvidenceAccumulator(margin=3.0, pause_s=1.0, min_average=0.4)
    assert feed(acc, [-0.1, 0.5, -0.1, -0.1])[0] == RIGHT


def test_rejected_windows_add_no_evidence():
    acc = EvidenceAccumulator(margin=3.0, pause_s=0.0)
    for k in range(1, 50):
        assert acc.update(k * 0.1, None) is None
    assert acc.stats() == (0.0, 0.0, 0.0)


def test_old_evidence_fades_after_the_memory():
    acc = EvidenceAccumulator(margin=3.0, pause_s=0.0, memory_s=3.0, min_average=0.0)
    t = 0.1
    for _ in range(30):                       # 3 s favouring up, but not enough to move
        acc.update(t, np.array([0.08, 0.0, 0.0, 0.0])); t += 0.1
    for _ in range(30):                       # then 3 s of nothing: up's lead should be gone
        acc.update(t, np.zeros(4)); t += 0.1
    assert acc.stats()[1] < 1e-9


def test_reference_levels_rescale_scores():
    ref = ReferenceLevels(not_looking=[0.3] * 4, looking=[0.7] * 4)
    np.testing.assert_allclose(ref.normalise(np.array([0.3, 0.7, 0.5, 0.9])), [0.0, 1.0, 0.5, 1.5])


def test_calibration_maths_on_a_clean_simulated_calibration():
    rng = np.random.default_rng(0)
    ticks = {1.0: [], 1.25: []}
    plan = ["maze", "maze"] + ["up", "right", "down", "left"] * 3
    for k, target in enumerate(plan):
        for i in range(1, 61 if target == "maze" else 31):
            for w in ticks:
                s = rng.normal(0.3, 0.03, 4)
                if target != "maze":
                    s[["up", "right", "down", "left"].index(target)] += 0.4
                ticks[w].append((k, target, i * 0.1, s))
    levels = reference_levels_from_calibration(ticks)
    np.testing.assert_allclose(levels[1.25].separation, 0.4, atol=0.05)
    check = check_presets_on_calibration(ticks, levels, memory_s=3.0, min_average=0.4)
    for name, r in check.items():
        assert r["correct"] == 12 and r["wrong"] == 0 and r["maze_false"] == 0, name
