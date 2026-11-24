import numpy as np
from scipy import signal

from ssvep_maze.signal_processing import (FlickerScorer, canonical_correlation, information_transfer_rate,
                                          make_live_filter, reference_waves)

FS, FREQS = 200, [11.0, 13.0, 15.0, 17.0]


def simulated_eeg(freq, seconds, amplitude=3.0, noise=10.0, seed=0):
    """Three channels of noise with a small SSVEP at `freq` (with a random phase), filtered like the game."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * FS)) / FS
    response = amplitude * np.sin(2 * np.pi * freq * t + rng.uniform(0, 2 * np.pi))
    x = rng.normal(0, noise, (len(t), 3)) + np.column_stack([0.8 * response, response, 0.8 * response])
    return signal.sosfilt(make_live_filter(FS), x, axis=0)


def test_reference_waves_skip_harmonics_near_nyquist():
    assert reference_waves(11.0, 200, FS).shape == (200, 6)       # 11, 22, 33 Hz all fine
    assert reference_waves(40.0, 200, FS).shape == (200, 4)       # 40, 80 Hz kept; 120 Hz is above 98 Hz


def test_cca_is_one_for_a_perfect_match_and_ignores_phase():
    ref = reference_waves(13.0, 400, FS)
    shifted = np.sin(2 * np.pi * 13.0 * np.arange(400) / FS + 1.0)[:, None]
    assert canonical_correlation(shifted, ref) > 0.999


def test_scorer_finds_the_flicker_frequency():
    scorer = FlickerScorer(FREQS, FS, window_s=1.25)
    for i, f in enumerate(FREQS):
        hits = 0
        for seed in range(20):
            scores = scorer.scores(simulated_eeg(f, 4.0, seed=seed), artifact_limit_uv=300)
            hits += int(np.argmax(scores) == i)
        assert hits >= 18, f


def test_scorer_rejects_artifacts_and_short_buffers():
    scorer = FlickerScorer(FREQS, FS, window_s=1.0)
    assert scorer.scores(np.zeros((50, 3)), 300) is None           # not enough data yet
    eeg = simulated_eeg(11.0, 2.0)
    eeg[-30:, 1] += 600                                            # a blink-sized jump
    assert scorer.scores(eeg, 300) is None


def test_information_transfer_rate():
    assert information_transfer_rate(4, 1.0, 1.0) == 120.0        # 2 bits per second
    assert information_transfer_rate(4, 0.25, 1.0) == 0.0         # chance level carries no information
