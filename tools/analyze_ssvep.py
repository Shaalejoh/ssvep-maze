#!/usr/bin/env python3
"""
Analyse a recording made with `ssvep_test.py record`.

    python analyze_ssvep.py data/sub-S01/ssvep-<timestamp>

What it reports
  * SNR at each flicker frequency: amplitude at that frequency divided by the mean amplitude of
    the neighbouring bins (0.3-1.5 Hz away). ~1 means nothing there; >2 is a clear response.
  * CCA classification: for each flicker trial, which frequency's sine/cosine reference
    (with harmonics) correlates best with the occipital channels. Reported for several window
    lengths, with chance level and a binomial p-value, because window length sets how fast the
    maze can respond.
  * Speed as information transfer rate (bits/min), a CCA confusion matrix, and a comparison
    with eCCA, which is calibrated on your own trials (leave-one-trial-out).
  * Rest/centre trials: the CCA score with no target, to compare against flicker trials (useful later
    for deciding when the user is not looking at any target).

Writes results.txt and spectra.png into the session folder.
"""
import csv
import json
import os
import sys

import numpy as np
from scipy import signal, stats

SSVEP_SITES = ("O1", "Oz", "O2", "POz", "PO3", "PO4", "PO7", "PO8", "Pz")
REJECT_PTP_UV = 400.0        # drop an epoch if any SSVEP channel swings more than this (movement, lost contact)
ONSET_SKIP_S = 0.5           # skip the visual onset response at the start of each flicker
N_HARMONICS = 3


def load(folder):
    meta = json.load(open(os.path.join(folder, "meta.json")))
    with open(os.path.join(folder, "raw.csv")) as f:
        header = f.readline().strip().split(",")
    raw = np.loadtxt(os.path.join(folder, "raw.csv"), delimiter=",", skiprows=1, ndmin=2)
    events = list(csv.DictReader(open(os.path.join(folder, "events.csv"))))
    return meta, header, raw, events


def preprocess(x, fs):
    """Band-pass 3-45 Hz and notch 60 Hz, zero-phase, per channel (x: samples x channels)."""
    x = signal.detrend(x, axis=0)
    sos = signal.butter(4, [3, min(45, 0.45 * fs)], btype="bandpass", fs=fs, output="sos")
    x = signal.sosfiltfilt(sos, x, axis=0)
    if fs > 125:
        b, a = signal.iirnotch(60.0, 30.0, fs)
        x = signal.filtfilt(b, a, x, axis=0)
    return x


def amp_spectrum(seg, fs, res=0.1):
    """Single-sided amplitude spectrum (uV), Hann window, zero-padded to `res` Hz bins."""
    n = seg.shape[0]
    nfft = max(n, int(2 ** np.ceil(np.log2(fs / res))))
    w = np.hanning(n)[:, None]
    X = np.fft.rfft((seg - seg.mean(0)) * w, n=nfft, axis=0)
    return np.fft.rfftfreq(nfft, 1 / fs), 2 * np.abs(X) / w.sum()


def snr_at(freqs, amp, f):
    """amp: bins x channels. Peak within +-0.15 Hz over mean of 0.3-1.5 Hz neighbours."""
    d = np.abs(freqs - f)
    peak = amp[d <= 0.15].max(0)
    ring = amp[(d >= 0.3) & (d <= 1.5)].mean(0)
    return peak / ring


def cca_corr(X, Y):
    """Largest canonical correlation between column sets X and Y (samples x features)."""
    qx, _ = np.linalg.qr(X - X.mean(0))
    qy, _ = np.linalg.qr(Y - Y.mean(0))
    return float(np.linalg.svd(qx.T @ qy, compute_uv=False)[0])


def cca_weights(X, Y):
    """Canonical weight vectors (wx, wy) for the largest canonical correlation."""
    Xc, Yc = X - X.mean(0), Y - Y.mean(0)
    qx, rx = np.linalg.qr(Xc)
    qy, ry = np.linalg.qr(Yc)
    u, s, vt = np.linalg.svd(qx.T @ qy)
    wx = np.linalg.lstsq(rx, u[:, 0], rcond=None)[0]
    wy = np.linalg.lstsq(ry, vt[0], rcond=None)[0]
    return wx, wy


def _corr(a, b):
    a, b = a - a.mean(), b - b.mean()
    d = np.sqrt((a @ a) * (b @ b))
    return float(a @ b / d) if d > 0 else 0.0


def ecca_score(X, Y, T):
    """Extended CCA (Chen et al. 2015): combines the sine/cosine reference Y with the
    individual template T (mean of the user's own training trials for that frequency)."""
    wxy, wyy = cca_weights(X, Y)
    wxt, _ = cca_weights(X, T)
    wty, _ = cca_weights(T, Y)
    r = [_corr(X @ wxy, Y @ wyy), _corr(X @ wxt, T @ wxt), _corr(X @ wxy, T @ wxy), _corr(X @ wty, T @ wty)]
    return sum(np.sign(v) * v * v for v in r)


def ecca_loo(X, labels, freqs, fs):
    """Leave-one-trial-out eCCA: templates are built only from the other trials."""
    X = np.asarray(X)
    labels = np.asarray(labels)
    n = X.shape[1]
    refs = [references(f, n, fs) for f in freqs]
    preds = []
    for i in range(len(X)):
        keep = np.ones(len(X), bool)
        keep[i] = False
        temps = [X[keep & (labels == k)].mean(0) for k in range(len(freqs))]
        preds.append(int(np.argmax([ecca_score(X[i], refs[k], temps[k]) for k in range(len(freqs))])))
    return preds


def references(f, n, fs):
    t = np.arange(n) / fs
    cols = []
    for h in range(1, N_HARMONICS + 1):
        if h * f < fs / 2 - 2:
            cols += [np.sin(2 * np.pi * h * f * t), np.cos(2 * np.pi * h * f * t)]
    return np.column_stack(cols)


def itr_bits_per_min(n, p, seconds):
    """Wolpaw information transfer rate."""
    if n < 2 or not np.isfinite(p):
        return float("nan")
    p = min(max(p, 1e-9), 1 - 1e-9)
    if p <= 1 / n:
        return 0.0
    bits = np.log2(n) + p * np.log2(p) + (1 - p) * np.log2((1 - p) / (n - 1))
    return bits * 60 / seconds


def analyze(folder):
    meta, header, raw, events = load(folder)
    fs = meta["sampling_rate_hz"]
    stim_s = meta["timing_s"]["stim"]
    freqs = [float(f) for f in meta["freqs_hz"]]
    names = meta["channel_names"]
    cols = [header.index(f"{n}_uV") for n in names]
    sites = [n for n in names if n in SSVEP_SITES] or names
    site_idx = [names.index(n) for n in sites]
    data = preprocess(raw[:, cols], fs)

    kept, rejected = [], []
    for ev in events:
        if ev["event"] != "stim" or ev["sample"] == "":
            continue
        s0 = int(ev["sample"]) + int(ONSET_SKIP_S * fs)
        s1 = int(ev["sample"]) + int(stim_s * fs)
        if s1 > len(data):
            continue
        seg = data[s0:s1]
        ptp = np.ptp(seg[:, site_idx], axis=0).max()
        (rejected if ptp > REJECT_PTP_UV else kept).append((ev["condition"], seg, ptp))
    if not kept:
        return ["No usable trials: every epoch was missing or exceeded the artifact limit."]

    lines = [f"SSVEP analysis: {folder}",
             f"Channels used: {', '.join(sites)}   fs={fs} Hz   epochs: {len(kept)} kept, "
             f"{len(rejected)} rejected (>{REJECT_PTP_UV:.0f} uV peak-to-peak)"]

    # ---- SNR table: rows = condition, cols = target frequency (averaged over SSVEP channels) ----
    conds = meta["conditions"]
    lines.append("\nSNR at each flicker frequency (mean over channels; noise alone gives ~1-1.3, >2 = clear peak)")
    lines.append("  condition   " + "".join(f"{f:>8g} Hz" for f in freqs) + "   best channel at own freq")
    spectra = {}
    for c in conds:
        segs = [seg for cc, seg, _ in kept if cc == c]
        if not segs:
            lines.append(f"  {c:<10}  (no kept trials)")
            continue
        fr, amps = zip(*(amp_spectrum(s, fs) for s in segs))
        mean_amp = np.mean(amps, axis=0)
        spectra[c] = (fr[0], mean_amp)
        row = [snr_at(fr[0], mean_amp[:, site_idx], f).mean() for f in freqs]
        best = ""
        if c != "rest":
            per_ch = snr_at(fr[0], mean_amp[:, site_idx], float(c))
            best = f"{sites[int(np.argmax(per_ch))]} ({per_ch.max():.1f})"
        lines.append(f"  {c:<10}  " + "".join(f"{v:>11.2f}" for v in row) + f"   {best}")

    # ---- CCA classification vs window length ----
    flick = [(cc, seg) for cc, seg, _ in kept if cc != "rest"]
    rest = [seg for cc, seg, _ in kept if cc == "rest"]
    chance = 1 / len(freqs)
    lines.append(f"\nCCA classification of flicker trials (chance = {100 * chance:.0f}%)")
    max_len = (stim_s - ONSET_SKIP_S)
    windows = [w for w in (1.0, 2.0, 3.0, max_len) if w <= max_len + 1e-9]
    windows = sorted(set(round(w, 2) for w in windows))
    cca_by_window = {}
    for w in windows:
        n = int(w * fs)
        refs = [references(f, n, fs) for f in freqs]
        correct = 0
        for c, seg in flick:
            X = seg[:n, site_idx]
            scores = [cca_corr(X, R) for R in refs]
            correct += int(abs(freqs[int(np.argmax(scores))] - float(c)) < 1e-6)
        k = len(flick)
        cca_by_window[w] = correct / max(k, 1)
        p = stats.binomtest(correct, k, chance, alternative="greater").pvalue if k else float("nan")
        lines.append(f"  {w:>4.1f} s window: {correct}/{k} correct = {100 * correct / max(k, 1):5.1f}%   "
                     f"p = {p:.2g}")

    # ---- speed (ITR), confusion, and a calibrated comparison (eCCA) ----
    labels = [freqs.index(float(c)) for c, _ in flick]
    per_class = [labels.count(k) for k in range(len(freqs))]
    lines.append("\nSpeed: Wolpaw ITR, assuming 0.5 s per selection to move your eyes on top of the window")
    has_cal = min(per_class) >= 3
    best = None
    for w in windows:
        acc = cca_by_window[w]
        bpm = itr_bits_per_min(len(freqs), acc, w + 0.5)
        extra = ""
        if has_cal:
            n = int(w * fs)
            X = [seg[:n, site_idx] for _, seg in flick]
            eacc = float(np.mean(np.array(ecca_loo(X, labels, freqs, fs)) == np.array(labels)))
            extra = f"   | calibrated eCCA (leave-one-out) {100 * eacc:5.1f}%"
        lines.append(f"  {w:>4.1f} s window: CCA {100 * acc:5.1f}%  ITR {bpm:5.1f} bits/min{extra}")
        if best is None or bpm > best[1]:
            best = (w, bpm)
    if len(freqs) > 2 and best:
        w = best[0]
        n = int(w * fs)
        refs = [references(f, n, fs) for f in freqs]
        pred = [int(np.argmax([cca_corr(seg[:n, site_idx], R) for R in refs])) for _, seg in flick]
        names_f = [f"{f:g}" for f in freqs]
        lines.append(f"  CCA confusion at the fastest window ({w:.1f} s); rows = looked at, cols = predicted:")
        lines.append("          " + "".join(f"{nm:>6}" for nm in names_f))
        for i, nm in enumerate(names_f):
            row = [sum(1 for l, q in zip(labels, pred) if l == i and q == j) for j in range(len(freqs))]
            lines.append(f"    {nm:>5} " + "".join(f"{v:>6d}" for v in row))

    # ---- rest vs flicker CCA scores (full window) ----
    n = int(max_len * fs)
    refs = [references(f, n, fs) for f in freqs]
    fl_scores = [max(cca_corr(seg[:n, site_idx], R) for R in refs) for _, seg in flick]
    if rest:
        rs_scores = [max(cca_corr(seg[:n, site_idx], R) for R in refs) for seg in rest]
        lines.append(f"\nBest CCA score, flicker trials: {np.mean(fl_scores):.3f} +- {np.std(fl_scores):.3f}   "
                     f"rest trials: {np.mean(rs_scores):.3f} +- {np.std(rs_scores):.3f}")

    # ---- verdict: a frequency counts as a clear response if its SNR during its own flicker is
    # >= 2 AND at least 1.5x the SNR at that frequency during rest (so background peaks don't count)
    clear, weak = [], []
    for f in freqs:
        c = f"{f:g}"
        if c not in spectra:
            continue
        own = snr_at(spectra[c][0], spectra[c][1][:, site_idx], f).mean()
        base = snr_at(spectra["rest"][0], spectra["rest"][1][:, site_idx], f).mean() if "rest" in spectra else 1.0
        (clear if own >= 2 and own >= 1.5 * base else weak).append(f"{f:g} Hz")
    full = int(max_len * fs)
    refs = [references(f, full, fs) for f in freqs]
    correct = sum(int(abs(freqs[int(np.argmax([cca_corr(seg[:full, site_idx], R) for R in refs]))] - float(c)) < 1e-6)
                  for c, seg in flick)
    p_full = stats.binomtest(correct, len(flick), chance, alternative="greater").pvalue if flick else 1.0
    acc_full = correct / len(flick) if flick else 0.0
    lines.append("")
    if p_full < 0.05 and acc_full >= 0.8:
        lines.append(f"Verdict: SSVEP detected. CCA identifies the flicker frequency in {100 * acc_full:.0f}% of trials "
                     f"(p = {p_full:.2g}).")
        if weak:
            lines.append(f"  Averaged spectral peaks are modest at {', '.join(weak)} (SNR < 2), but CCA still separates them.")
    elif clear and not weak and p_full < 0.05:
        lines.append(f"Verdict: clear SSVEP at {', '.join(clear)}, and CCA tells them apart above chance (p = {p_full:.2g}).")
    elif clear or p_full < 0.05:
        lines.append(f"Verdict: partial. Clear at {', '.join(clear) or 'none'}; weak at {', '.join(weak) or 'none'}; "
                     f"CCA p = {p_full:.2g}. Check contact on O1/Oz/O2 and fixation on the cross.")
    else:
        lines.append("Verdict: no clear SSVEP yet. Check contact, fixation on the cross, screen brightness, "
                     "and that the monitor refresh was measured correctly.")

    with open(os.path.join(folder, "results.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    plot(folder, spectra, sites, names, freqs)
    return lines


def plot(folder, spectra, sites, names, freqs):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    fig, axes = plt.subplots(len(sites), 1, figsize=(9, 2.6 * len(sites)), sharex=True, squeeze=False)
    colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    for i, site in enumerate(sites):
        ax = axes[i, 0]
        ch = names.index(site)
        for j, (c, (fr, amp)) in enumerate(spectra.items()):
            m = (fr >= 3) & (fr <= 45)
            style = dict(color="0.55", lw=1.2, ls="--") if c == "rest" else dict(color=colors[j % len(colors)], lw=1.4)
            ax.plot(fr[m], amp[m, ch], label=("rest (no flicker)" if c == "rest" else f"{c} Hz flicker"), **style)
        for j, f in enumerate(freqs):
            for h in range(1, N_HARMONICS + 1):
                if h * f <= 45:
                    ax.axvline(h * f, color=colors[j % len(colors)], alpha=0.25 if h > 1 else 0.5, lw=1)
        ax.set_ylabel(f"{site}\namplitude (uV)")
        ax.set_yscale("log")
        ax.grid(alpha=0.2)
    axes[0, 0].legend(loc="upper right", fontsize=8)
    axes[0, 0].set_title("Mean spectrum per condition (vertical lines: flicker frequencies and harmonics)")
    axes[-1, 0].set_xlabel("frequency (Hz)")
    fig.tight_layout()
    fig.savefig(os.path.join(folder, "spectra.png"), dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python analyze_ssvep.py <session folder>")
    print("\n".join(analyze(sys.argv[1])))
