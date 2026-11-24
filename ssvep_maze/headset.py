"""
Getting EEG in: connecting to the OpenBCI Ganglion through BrainFlow, recording everything to
disk, and keeping a live filtered buffer for the classifier. Also a simulated headset for
trying the game without hardware.
"""
import csv
import os
import sys
import time

import numpy as np
from scipy import signal

from .settings import BUFFER_SECONDS, VISUAL_CHANNEL_NAMES
from .signal_processing import make_live_filter


# ----------------------------------------------------------------------------- real hardware
def find_dongle():
    """Serial port of the BLED112 Bluetooth dongle that ships with the Ganglion, if plugged in."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    for port in list_ports.comports():
        if (port.vid, port.pid) == (0x2458, 0x0001) or "bluegiga" in (port.description or "").lower():
            return port.device
    return None


def open_board(args):
    """Connect to the board chosen on the command line. Returns (BrainFlow board, board id)."""
    from brainflow.board_shim import BoardIds, BoardShim, BrainFlowInputParams
    params = BrainFlowInputParams()
    if args.board == "synthetic":
        board_id = BoardIds.SYNTHETIC_BOARD
    else:
        board_id = BoardIds.GANGLION_BOARD if args.board == "ganglion" else BoardIds.GANGLION_NATIVE_BOARD
        if args.board == "ganglion":
            params.serial_port = args.serial_port or find_dongle() or ""
            if not params.serial_port:
                sys.exit("Could not find the BLED112 dongle. Plug it in, or pass --serial-port COM<n>.")
            params.timeout = 15
        if args.mac_address:
            params.mac_address = args.mac_address
        # BrainFlow quirk (dongle, no MAC address): with a fixed firmware setting ("fw:2", or none,
        # which means 3) it fails immediately with error 13 and never opens the device. "fw:auto"
        # connects and detects the firmware itself, so always use it on that path.
        if args.board == "ganglion" and not args.mac_address:
            params.other_info = "fw:auto"
        else:
            params.other_info = f"fw:{args.firmware}"
    if args.verbose:
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


def board_channel_layout(board_id):
    """Sample rate, and which rows of BrainFlow's data array hold EEG, markers, timestamps etc."""
    from brainflow.board_shim import BoardShim
    try:
        accel = BoardShim.get_accel_channels(board_id)
    except Exception:
        accel = []
    try:
        package = BoardShim.get_package_num_channel(board_id)
    except Exception:
        package = None
    return BoardShim.get_sampling_rate(board_id), {
        "eeg": BoardShim.get_eeg_channels(board_id), "marker": BoardShim.get_marker_channel(board_id),
        "ts": BoardShim.get_timestamp_channel(board_id), "pkg": package, "accel": accel}


# ----------------------------------------------------------------------------- simulated headset
class SimulatedHeadset:
    """Stands in for the Ganglion so the game can run without hardware (--board sim).

    Generates, in real time at 200 Hz on 4 channels:
      - drifting background EEG (~10 uV) plus 3 uV of white noise
      - a 4 uV, 9.5 Hz alpha rhythm that waxes and wanes
      - an SSVEP for every button: 3 uV for the one being "looked at" (after a 1 s gaze shift),
        0.6 uV for the others (flicker leaking in from the edge of vision), strongest on Oz.
    The game tells it where the simulated player is looking with look_at(); the player always looks
    at the correct next button. Good for testing the software, not for predicting real performance.
    """
    sample_rate = 200
    layout = {"eeg": [0, 1, 2, 3], "marker": 4, "ts": 5, "pkg": None, "accel": []}

    def __init__(self, freqs_hz, seed=0):
        self.freqs, self.rng = freqs_hz, np.random.default_rng(seed)
        self.gaze, self.gaze_since = None, 0.0
        self.samples_sent, self.t0, self.drift = 0, None, np.zeros(4)
        self.pending_markers = []

    def look_at(self, freq_hz):
        if freq_hz != self.gaze:
            self.gaze, self.gaze_since = freq_hz, time.perf_counter()

    # The same methods BrainFlow's BoardShim has, so the game can't tell the difference.
    def prepare_session(self):
        pass

    def start_stream(self, *_):
        self.t0 = time.perf_counter()

    def insert_marker(self, code):
        self.pending_markers.append(code)

    def get_board_data(self):
        now = time.perf_counter()
        n = int((now - self.t0) * self.sample_rate) - self.samples_sent
        if n <= 0:
            return np.zeros((6, 0))
        t = (self.samples_sent + np.arange(n)) / self.sample_rate
        out = np.zeros((6, n))
        for k in range(n):                                   # slowly drifting background
            self.drift = 0.97 * self.drift + self.rng.standard_normal(4) * 2.4
            out[:4, k] = self.drift
        out[:4] += 3 * self.rng.standard_normal((4, n))      # white noise
        out[:3] += 4 * np.sin(2 * np.pi * 9.5 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.13 * t))   # alpha
        for f in self.freqs:
            focused = self.gaze == f and now - self.gaze_since > 1.0
            amp = 3.0 if focused else 0.6
            wave = amp * (np.sin(2 * np.pi * f * (t - 0.14)) + 0.4 * np.sin(4 * np.pi * f * (t - 0.14)))
            out[0] += 0.8 * wave; out[1] += wave; out[2] += 0.8 * wave; out[3] += 0.2 * wave   # O1, Oz, O2, Cz
        out[5] = time.time() - (n - 1 - np.arange(n)) / self.sample_rate
        if self.pending_markers:
            out[4, 0] = self.pending_markers.pop(0)
        self.samples_sent += n
        return out

    def stop_stream(self):
        pass

    def release_session(self):
        pass


# ----------------------------------------------------------------------------- recording
class Recorder:
    """Pulls new samples from the board, appends them to raw.csv, keeps a causally filtered buffer
    of the visual channels for the classifier, and logs events. At the end, events.csv gets the
    exact sample number of each event, found from the marker the board stamped into the data."""

    def __init__(self, board, sample_rate, layout, channel_names, folder):
        self.board, self.sample_rate, self.layout = board, sample_rate, layout
        n_eeg = len(layout["eeg"])
        self.names = list(channel_names[:n_eeg]) + [f"ch{i + 1}" for i in range(len(channel_names), n_eeg)]
        self.visual = [i for i, name in enumerate(self.names) if name in VISUAL_CHANNEL_NAMES]
        if not self.visual:
            raise SystemExit(f"No visual-cortex channels among {self.names}; name them O1, Oz, O2 (or POz ...).")

        has_pkg = layout["pkg"] is not None
        self.rows_to_save = (([layout["pkg"]] if has_pkg else []) + layout["eeg"] + layout["accel"]
                             + [layout["ts"], layout["marker"]])
        self.header = (["sample"] + (["package_num"] if has_pkg else []) + [f"{n}_uV" for n in self.names]
                       + [f"accel_{ax}" for ax in "xyz"[:len(layout["accel"])]] + ["unix_time", "marker"])
        self.raw = open(os.path.join(folder, "raw.csv"), "w", newline="")
        self.raw.write(",".join(self.header) + "\n")
        self.events_path = os.path.join(folder, "events.csv")
        self.n, self.events, self.marker_samples = 0, [], []

        self.filter = make_live_filter(sample_rate)
        self.filter_state = np.zeros((self.filter.shape[0], 2, len(self.visual)))
        self.buf = np.zeros((0, len(self.visual)))           # filtered visual channels, newest last
        self.buffer_samples = int(sample_rate * BUFFER_SECONDS)

    def pull(self):
        """Fetch whatever the board has collected since last time. Returns the number of samples."""
        data = self.board.get_board_data()
        if data.shape[1] == 0:
            return 0
        block = data[self.rows_to_save].T
        index = np.arange(self.n, self.n + block.shape[0])
        for i in np.nonzero(block[:, -1])[0]:                # non-zero marker column = an event happened here
            self.marker_samples.append((int(index[i]), int(block[i, -1])))
        n_pre = 1 if self.layout["pkg"] is not None else 0
        fmt = ["%d"] * (1 + n_pre) + ["%.4f"] * (len(self.names) + len(self.layout["accel"])) + ["%.6f", "%d"]
        np.savetxt(self.raw, np.column_stack([index, block]), delimiter=",", fmt=fmt)

        eeg = data[[self.layout["eeg"][i] for i in self.visual]].T
        filtered, self.filter_state = signal.sosfilt(self.filter, eeg, axis=0, zi=self.filter_state)
        self.buf = np.vstack([self.buf, filtered])[-self.buffer_samples:]
        self.n += block.shape[0]
        return block.shape[0]

    def mark(self, code, kind, **info):
        """Stamp a marker into the EEG stream and remember what it meant."""
        self.board.insert_marker(code)
        self.events.append({"event": kind, "marker": code, "unix_time": f"{time.time():.4f}", **info})

    def close(self):
        """Write events.csv, matching each logged event to its marker's sample number. Returns how many matched."""
        time.sleep(0.3)
        self.pull()
        self.raw.close()
        first = ["event", "marker", "unix_time"]
        keys = first + sorted({k for e in self.events for k in e} - set(first))
        unmatched, matched = list(self.marker_samples), 0
        with open(self.events_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys + ["sample"])
            w.writeheader()
            for ev in self.events:
                sample = ""
                for j, (s, code) in enumerate(unmatched):
                    if code == ev["marker"]:
                        sample = s
                        del unmatched[:j + 1]
                        matched += 1
                        break
                w.writerow({**ev, "sample": sample})
        return matched
