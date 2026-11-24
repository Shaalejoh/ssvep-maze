"""
Turning raw EEG into "how strongly is each button's flicker present right now?"

When you look at a light flickering at f Hz, your visual cortex produces a small oscillation at
f Hz (and at 2f, 3f...). This is called an SSVEP (steady-state visually evoked potential).

To measure it we use CCA (canonical correlation analysis). For each button we build clean sine and
cosine waves at its frequency and harmonics, then ask: what is the best correlation we can get
between some weighted mix of the EEG channels and some weighted mix of those reference waves?
A score near 1 means that frequency is clearly present; around 0.3 is the usual background level
for a 1-second window of three channels. Using sines AND cosines makes the score ignore the phase
of the response, so it doesn't matter exactly when the flicker started.

This module has no pygame or hardware code, so it can be tested on its own.
"""
import numpy as np
from scipy import signal

from .settings import BANDPASS_HZ, MAINS_HZ, REFERENCE_HARMONICS


def reference_waves(freq_hz, n_samples, sample_rate):
    """Sine and cosine columns at freq_hz and its harmonics (skipping any too close to Nyquist)."""
    t = np.arange(n_samples) / sample_rate
    columns = []
    for harmonic in range(1, REFERENCE_HARMONICS + 1):
        f = harmonic * freq_hz
        if f < sample_rate / 2 - 2:
            columns += [np.sin(2 * np.pi * f * t), np.cos(2 * np.pi * f * t)]
    return np.column_stack(columns)


def canonical_correlation(eeg, reference):
    """Largest canonical correlation between two sets of columns (rows are time samples).

    Both sets are centred and orthonormalised with a QR decomposition; the singular values of
    Qx^T Qy are then exactly the canonical correlations, and we want the largest one.
    """
    qx, _ = np.linalg.qr(eeg - eeg.mean(axis=0))
    qy, _ = np.linalg.qr(reference - reference.mean(axis=0))
    return float(np.linalg.svd(qx.T @ qy, compute_uv=False)[0])


def make_live_filter(sample_rate):
    """Band-pass plus mains notch, as second-order sections.

    The game filters causally (only past samples), exactly as it must live. A notch is added
    only if the sample rate is high enough to contain mains frequency.
    """
    low, high = BANDPASS_HZ
    sections = [signal.butter(4, [low, min(high, 0.45 * sample_rate)], btype="bandpass", fs=sample_rate, output="sos")]
    if sample_rate > 125:
        b, a = signal.iirnotch(MAINS_HZ, 30.0, sample_rate)
        sections.append(signal.tf2sos(b, a))
    return np.vstack(sections)


class FlickerScorer:
    """Scores the latest window of filtered EEG against every button's frequency.

    One scorer per window length. The reference waves are built once and reused.
    """

    def __init__(self, freqs_hz, sample_rate, window_s):
        self.n_samples = int(window_s * sample_rate)
        self.references = [reference_waves(f, self.n_samples, sample_rate) for f in freqs_hz]

    def scores(self, filtered_eeg, artifact_limit_uv):
        """One CCA score per button, or None if there isn't enough data yet or the window has an artifact.

        filtered_eeg: samples x channels, newest last.
        """
        if len(filtered_eeg) < self.n_samples:
            return None
        window = filtered_eeg[-self.n_samples:]
        if np.ptp(window, axis=0).max() > artifact_limit_uv:      # blink, movement or lost contact
            return None
        return np.array([canonical_correlation(window, ref) for ref in self.references])


def information_transfer_rate(n_choices, accuracy, seconds_per_choice):
    """Wolpaw information transfer rate in bits per minute: a standard BCI speed-and-accuracy figure."""
    if n_choices < 2 or not np.isfinite(accuracy) or seconds_per_choice <= 0:
        return float("nan")
    p = min(max(accuracy, 1e-9), 1.0)
    bits = np.log2(n_choices) + p * np.log2(p)
    if p < 1:
        bits += (1 - p) * np.log2((1 - p) / (n_choices - 1))
    return max(float(bits), 0.0) * 60.0 / seconds_per_choice
