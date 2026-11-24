#!/usr/bin/env python3
"""
SSVEP flicker test for the OpenBCI Ganglion (BrainFlow + pygame).

A large square flickers with a sinusoidal brightness at a set frequency, locked to the
monitor's refresh. Sinusoidal modulation (rather than on/off) gives a clean response at
any frequency, even ones that don't divide the refresh rate evenly.

WARNING: flickering light can trigger seizures in people with photosensitive epilepsy.
Do not use this if that applies to you or the person wearing the headset.

Two modes
---------
live    No recording. Flickers continuously so you can watch the OpenBCI GUI's FFT at the
        same time (run in a window beside the GUI). The GUI keeps the board.
            python ssvep_test.py live --freq 12 --windowed
            keys: 1-9 pick a frequency from --freqs, SPACE pause/resume, ESC quit

record  Records the board itself (close the OpenBCI GUI first), with a marker at every
        stimulus. Trials of each frequency plus no-flicker "rest" trials, in random order.
        Analysis runs automatically at the end.
            python ssvep_test.py record --channels O1,Oz,O2,Cz
            python ssvep_test.py record --board synthetic --windowed      (dry run, no headset)

Install:  pip install brainflow pygame pyserial numpy scipy matplotlib

Output (record):  data/sub-<subject>/ssvep-<timestamp>/
  raw.csv, events.csv, meta.json      (same layout as collect_mi.py)
  results.txt, spectra.png            (from analyze_ssvep.py)

Marker codes: 1..N = flicker onset at freqs[0..N-1], 50 = rest onset (no flicker),
              10 = fixation onset, 20 = stimulus offset
"""
import argparse
import csv
import json
import math
import os
import random
import sys
import time
from datetime import datetime

import numpy as np
import pygame
from brainflow.board_shim import BoardIds, BoardShim, BrainFlowInputParams

FIXATION_CODE, STIM_OFF_CODE, REST_CODE = 10, 20, 50
BG, FG, FIX = (0, 0, 0), (235, 235, 235), (255, 60, 60)
MAINS_ALIAS_HZ = 20.0   # 180 Hz mains harmonic folds to 20 Hz at the Ganglion's 200 Hz sampling


# ----------------------------------------------------------------------------------- args
def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["live", "record"])
    p.add_argument("--freqs", default="12,15", help="flicker frequencies in Hz, comma separated")
    p.add_argument("--freq", type=float, default=None, help="live mode: starting frequency")
    p.add_argument("--subject", default="S01")
    p.add_argument("--board", choices=["ganglion", "ganglion-native", "synthetic"], default="ganglion")
    p.add_argument("--serial-port", default=None)
    p.add_argument("--mac-address", default=None)
    p.add_argument("--firmware", choices=["2", "3", "auto"], default="auto",
                   help="Ganglion firmware; the dongle path always auto-detects (see open_board)")
    p.add_argument("--verbose", action="store_true", help="show BrainFlow's detailed connection log")
    p.add_argument("--channels", default="O1,Oz,O2,Cz", help="names for Ganglion inputs +1..+4, in order")
    p.add_argument("--trials", type=int, default=10, help="record: trials per condition (each freq and rest)")
    p.add_argument("--no-rest", action="store_true", help="record: leave out the no-flicker rest trials")
    p.add_argument("--fixation", type=float, default=2.0)
    p.add_argument("--stim", type=float, default=5.0, help="seconds of flicker per trial")
    p.add_argument("--iti-min", type=float, default=2.0, help="blank screen between trials, min seconds")
    p.add_argument("--iti-max", type=float, default=3.0)
    p.add_argument("--size", type=float, default=0.45, help="square size as a fraction of the screen height")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--out", default="data")
    p.add_argument("--windowed", action="store_true")
    p.add_argument("--autostart", action="store_true", help="skip key presses (testing)")
    a = p.parse_args()
    a.freqs = [float(f) for f in a.freqs.split(",") if f.strip()]
    if not a.freqs:
        p.error("--freqs is empty")
    a.channels = [c.strip() for c in a.channels.split(",") if c.strip()]
    if a.freq is None:
        a.freq = a.freqs[0]
    return a


# ----------------------------------------------------------------------------------- display
class Display:
    def __init__(self, windowed, size_frac):
        pygame.init()
        self.vsync = True
        if windowed:
            size, flags = (800, 800), pygame.SCALED
        else:
            size, flags = pygame.display.get_desktop_sizes()[0], pygame.FULLSCREEN | pygame.SCALED
        try:
            self.surface = pygame.display.set_mode(size, flags, vsync=1)
        except pygame.error:
            self.vsync = False
            self.surface = pygame.display.set_mode(size, flags)
        pygame.display.set_caption("SSVEP flicker test")
        pygame.mouse.set_visible(windowed)
        self.w, self.h = self.surface.get_size()
        side = int(min(self.w, self.h) * size_frac)
        self.square = pygame.Rect(0, 0, side, side)
        self.square.center = (self.w // 2, self.h // 2)
        self.font = pygame.font.SysFont(None, max(26, self.h // 26))
        self.small = pygame.font.SysFont(None, max(20, self.h // 40))
        self.refresh_hz, self.frame_s = self.measure_refresh()

    def measure_refresh(self, frames=120):
        """Flip a blank screen repeatedly and time it. With vsync each flip waits for the next refresh."""
        stamps = []
        for _ in range(frames):
            pump()
            self.surface.fill(BG)
            pygame.display.flip()
            stamps.append(time.perf_counter())
        gaps = np.diff(stamps[10:])
        med = float(np.median(gaps))
        if not self.vsync or med < 1 / 400:          # flips not waiting for the monitor
            self.vsync = False
            return 60.0, 1 / 60.0
        return 1.0 / med, med

    def fixation(self, color=FIX):
        c, s, t = self.square.center, max(8, self.h // 90), max(2, self.h // 400)
        pygame.draw.line(self.surface, color, (c[0] - s, c[1]), (c[0] + s, c[1]), t)
        pygame.draw.line(self.surface, color, (c[0], c[1] - s), (c[0], c[1] + s), t)

    def frame(self, level, fixation=True, label=None):
        """Draw the square at brightness level 0..1 and flip; returns the flip time."""
        self.surface.fill(BG)
        g = int(round(255 * min(1.0, max(0.0, level))))
        pygame.draw.rect(self.surface, (g, g, g), self.square)
        if fixation:
            self.fixation()
        if label:
            img = self.small.render(label, True, FG)
            self.surface.blit(img, (12, 10))
        pygame.display.flip()
        return time.perf_counter()

    def text(self, lines, small=()):
        self.surface.fill(BG)
        y = self.h // 2 - (len(lines) + len(small)) * self.font.get_linesize() // 2
        for font, group, color in ((self.font, lines, FG), (self.small, small, (150, 150, 150))):
            for line in group:
                img = font.render(line, True, color)
                self.surface.blit(img, img.get_rect(center=(self.w // 2, y)))
                y += font.get_linesize()
            y += font.get_linesize() // 2
        pygame.display.flip()


class Abort(Exception):
    pass


def pump(keys=()):
    hit = None
    for ev in pygame.event.get():
        if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE):
            raise Abort
        if ev.type == pygame.KEYDOWN and ev.key in keys:
            hit = ev.key
    return hit


def flicker(disp, freq, seconds, on_first_flip=None):
    """Sinusoidal flicker for `seconds`. Brightness follows the frame count, so the
    frequency stays exact as long as no frames are dropped. Returns (frames, dropped)."""
    n_frames = int(round(seconds * disp.refresh_hz))
    dropped, last, t0 = 0, None, time.perf_counter()
    for n in range(n_frames):
        pump()
        if not disp.vsync:                      # no vsync: pace frames with the clock instead
            wait = t0 + n * disp.frame_s - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
        level = 0.5 * (1 + math.sin(2 * math.pi * freq * n * disp.frame_s))
        t = disp.frame(level)
        if n == 0 and on_first_flip:
            on_first_flip()
        if last is not None and t - last > 1.5 * disp.frame_s:
            dropped += int(round((t - last) / disp.frame_s)) - 1
        last = t
    return n_frames, dropped


def steady(disp, seconds, level=0.5, fixation=True, on_first_flip=None, during=None):
    """Static square (mean brightness) for `seconds`, refreshed every frame for steady timing."""
    end = time.perf_counter() + seconds
    first = True
    while time.perf_counter() < end:
        pump()
        disp.frame(level, fixation)
        if first and on_first_flip:
            on_first_flip()
            first = False
        if during:
            during()


def blank(disp, seconds, during=None):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pump()
        disp.surface.fill(BG)
        pygame.display.flip()
        if during:
            during()


def wait_key(disp, lines, small, autostart, keys=(pygame.K_SPACE,)):
    disp.text(lines, small)
    if autostart:
        return
    while not pump(keys):
        time.sleep(0.02)


# ----------------------------------------------------------------------------------- board
def find_dongle():
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    for port in list_ports.comports():
        if (port.vid, port.pid) == (0x2458, 0x0001) or "bluegiga" in (port.description or "").lower():
            return port.device
    return None


def open_board(a):
    params = BrainFlowInputParams()
    if a.board == "synthetic":
        board_id = BoardIds.SYNTHETIC_BOARD
    else:
        board_id = BoardIds.GANGLION_BOARD if a.board == "ganglion" else BoardIds.GANGLION_NATIVE_BOARD
        if a.board == "ganglion":
            params.serial_port = a.serial_port or find_dongle() or ""
            if not params.serial_port:
                sys.exit("Could not find the BLED112 dongle. Plug it in, or pass --serial-port COM<n>.")
            params.timeout = 15
        if a.mac_address:
            params.mac_address = a.mac_address
        # BrainFlow bug (dongle, no MAC address): with a fixed firmware ("fw:2", or no setting,
        # which means 3) it never opens the device and fails at once with code 13. Only "fw:auto"
        # actually connects, and it detects the firmware itself, so always use it on that path.
        if a.board == "ganglion" and not a.mac_address:
            params.other_info = "fw:auto"
        else:
            params.other_info = f"fw:{a.firmware}"
    if a.verbose:
        BoardShim.enable_dev_board_logger()
    else:
        BoardShim.disable_board_logger()
    board = BoardShim(board_id, params)
    try:
        board.prepare_session()
    except Exception as exc:
        sys.exit(f"Could not connect to the board: {exc}\nCheck: board on (blue LED blinking slowly), "
                 "OpenBCI GUI closed, battery charged, board near the dongle.\n"
                 "Rerun with --verbose to see BrainFlow's log, or try --board ganglion-native.")
    return board, int(board_id)


class Recorder:
    """Pulls samples into raw.csv during quiet periods and logs events (same format as collect_mi.py)."""

    def __init__(self, board, board_id, channel_names, folder):
        self.board = board
        eeg = BoardShim.get_eeg_channels(board_id)
        self.fs = BoardShim.get_sampling_rate(board_id)
        names = list(channel_names[:len(eeg)]) + [f"ch{i + 1}" for i in range(len(channel_names), len(eeg))]
        try:
            accel = BoardShim.get_accel_channels(board_id)
        except Exception:
            accel = []
        self.rows = ([BoardShim.get_package_num_channel(board_id)] + eeg + accel
                     + [BoardShim.get_timestamp_channel(board_id), BoardShim.get_marker_channel(board_id)])
        self.header = (["sample", "package_num"] + [f"{n}_uV" for n in names]
                       + [f"accel_{ax}" for ax in "xyz"[:len(accel)]] + ["unix_time", "marker"])
        self.channel_names = names
        self.n_samples, self.marker_samples, self.events = 0, [], []
        self.raw_path, self.events_path = os.path.join(folder, "raw.csv"), os.path.join(folder, "events.csv")
        self.raw_file = open(self.raw_path, "w", newline="")
        self.raw_file.write(",".join(self.header) + "\n")

    def mark(self, code, trial, event, condition=""):
        self.board.insert_marker(code)
        self.events.append([trial, event, condition, code, f"{time.time():.4f}"])

    def drain(self):
        data = self.board.get_board_data()
        if data.shape[1] == 0:
            return
        block = data[self.rows].T
        idx = np.arange(self.n_samples, self.n_samples + block.shape[0])
        for i in np.nonzero(block[:, -1])[0]:
            self.marker_samples.append((int(idx[i]), int(block[i, -1])))
        fmt = ["%d", "%d"] + ["%.4f"] * (block.shape[1] - 3) + ["%.6f", "%d"]
        np.savetxt(self.raw_file, np.column_stack([idx, block]), delimiter=",", fmt=fmt)
        self.raw_file.flush()
        self.n_samples += block.shape[0]

    def close(self):
        time.sleep(0.3)
        self.drain()
        self.raw_file.close()
        matched, pool = 0, list(self.marker_samples)
        with open(self.events_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["trial", "event", "condition", "marker", "unix_time", "sample"])
            for ev in self.events:
                sample = ""
                for j, (s, code) in enumerate(pool):
                    if code == ev[3]:
                        sample = s
                        del pool[:j + 1]
                        matched += 1
                        break
                w.writerow(ev + [sample])
        return matched


# ----------------------------------------------------------------------------------- modes
WARNING = ["WARNING: this test shows flickering light.",
           "Do not continue if you have photosensitive epilepsy",
           "or have ever had a seizure from flashing lights."]


def run_live(a, disp):
    freqs = list(a.freqs)
    if a.freq not in freqs:
        freqs.insert(0, a.freq)
    idx, paused = freqs.index(a.freq), False
    keymap = {getattr(pygame, f"K_{i + 1}"): i for i in range(min(9, len(freqs)))}
    wait_key(disp, WARNING, ["Live mode: watch the OpenBCI GUI FFT for a peak at the flicker frequency.",
                             "Keys: " + "  ".join(f"{i + 1}={f:g} Hz" for i, f in enumerate(freqs[:9]))
                             + "   SPACE pause   ESC quit", "", "Press SPACE to start."], a.autostart)
    n = 0
    while True:
        key = pump(tuple(keymap) + (pygame.K_SPACE,))
        if key == pygame.K_SPACE:
            paused = not paused
        elif key in keymap:
            idx, n = keymap[key], 0
        f = freqs[idx]
        level = 0.5 if paused else 0.5 * (1 + math.sin(2 * math.pi * f * n * disp.frame_s))
        disp.frame(level, label=f"{'PAUSED' if paused else f'{f:g} Hz'}   refresh {disp.refresh_hz:.1f} Hz")
        n += 1


def run_record(a, disp):
    seed = a.seed if a.seed is not None else random.SystemRandom().randrange(1_000_000)
    rng = random.Random(seed)
    conds = [f"{f:g}" for f in a.freqs] + ([] if a.no_rest else ["rest"])
    order = [c for c in conds for _ in range(a.trials)]
    for _ in range(2000):                       # shuffle, no condition more than 3 times in a row
        rng.shuffle(order)
        if all(len(set(order[i:i + 4])) > 1 for i in range(len(order) - 3)):
            break
    codes = {f"{f:g}": i + 1 for i, f in enumerate(a.freqs)}
    codes["rest"] = REST_CODE

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = os.path.join(a.out, f"sub-{a.subject}", f"ssvep-{stamp}")
    os.makedirs(folder, exist_ok=True)
    board, board_id = open_board(a)
    rec = Recorder(board, board_id, a.channels, folder)
    meta = {"paradigm": "ssvep", "subject": a.subject, "session": stamp, "board": a.board,
            "board_id": board_id, "sampling_rate_hz": rec.fs, "channel_names": rec.channel_names,
            "freqs_hz": a.freqs, "conditions": conds, "marker_codes": {**codes, "fixation": FIXATION_CODE,
            "stim_off": STIM_OFF_CODE}, "timing_s": {"fixation": a.fixation, "stim": a.stim,
            "iti_min": a.iti_min, "iti_max": a.iti_max}, "trials_per_condition": a.trials,
            "trial_order": order, "seed": seed, "refresh_hz": round(disp.refresh_hz, 3),
            "vsync": disp.vsync, "stimulus": "sinusoidal luminance, square %.0f%% of screen height" % (100 * a.size),
            "raw_columns": rec.header, "completed": False}
    meta_path = os.path.join(folder, "meta.json")
    json.dump(meta, open(meta_path, "w"), indent=2)

    near_alias = [f for f in a.freqs if any(abs(k * f - MAINS_ALIAS_HZ) < 0.6 for k in (1, 2, 3))]
    notes = [f"Monitor refresh measured at {disp.refresh_hz:.1f} Hz"
             + ("" if disp.vsync else "  (VSYNC NOT WORKING: frequencies will not be exact)")]
    if near_alias:
        notes.append(f"Note: {near_alias} Hz has a harmonic near 20 Hz, where mains noise shows up.")
    minutes = len(order) * (a.fixation + a.stim + (a.iti_min + a.iti_max) / 2) / 60
    aborted, drops = False, []
    try:
        board.start_stream(450000)
        time.sleep(1.0)
        board.get_board_data()
        wait_key(disp, WARNING,
                 [f"{len(order)} trials ({', '.join(conds)}), about {minutes:.0f} min.",
                  "Look at the red cross in the centre of the square the whole time.",
                  "Stay still, relax your jaw. Blink only when the screen is black.", *notes, "",
                  "SPACE = start    ESC = stop (data so far is kept)"], a.autostart)
        blank(disp, 2.0, during=rec.drain)
        for trial, cond in enumerate(order, start=1):
            steady(disp, a.fixation, on_first_flip=lambda t=trial: rec.mark(FIXATION_CODE, t, "fixation"))
            if cond == "rest":
                steady(disp, a.stim, on_first_flip=lambda t=trial: rec.mark(REST_CODE, t, "stim", "rest"))
            else:
                _, d = flicker(disp, float(cond), a.stim,
                               on_first_flip=lambda t=trial, c=cond: rec.mark(codes[c], t, "stim", c))
                drops.append(d)
            disp.surface.fill(BG); pygame.display.flip()
            rec.mark(STIM_OFF_CODE, trial, "stim_off", cond)
            blank(disp, rng.uniform(a.iti_min, a.iti_max), during=rec.drain)
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
                 "duration_s": round(rec.n_samples / rec.fs, 1),
                 "events_logged": len(rec.events), "events_found_in_recording": matched,
                 "dropped_frames_per_flicker_trial": drops})
    json.dump(meta, open(meta_path, "w"), indent=2)
    print(f"\n{'Stopped early' if aborted else 'Session complete'}: {folder}")
    print(f"  {rec.n_samples} samples ({rec.n_samples / rec.fs:.0f} s), "
          f"markers found {matched}/{len(rec.events)}, refresh {disp.refresh_hz:.1f} Hz, "
          f"dropped frames total {sum(drops)}")

    summary = []
    try:
        from analyze_ssvep import analyze
        summary = analyze(folder)
    except Exception as exc:
        summary = [f"Analysis failed: {exc}", "Run: python analyze_ssvep.py " + folder]
    print("\n".join(summary))
    if not a.autostart:
        try:
            wait_key(disp, ["Done."] + summary[:12], ["Results saved in " + folder, "Press SPACE or ESC to close."],
                     False)
        except Abort:
            pass


def main():
    a = parse_args()
    disp = None
    try:
        disp = Display(a.windowed, a.size)
        if a.mode == "live":
            run_live(a, disp)
        else:
            run_record(a, disp)
    except Abort:
        pass
    finally:
        pygame.quit()


if __name__ == "__main__":
    main()
