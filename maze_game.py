#!/usr/bin/env python3
"""
SSVEP Maze: steer a maze by looking at flickering buttons, read from EEG with an OpenBCI Ganglion.

Look at one of the four flickering buttons to move that way; look at the maze to stay still.

WARNING: flickering light can trigger seizures in people with photosensitive epilepsy.

    python maze_game.py                         headset on the BLED112 dongle (inputs named O1,Oz,O2,Cz)
    python maze_game.py --board sim             simulated headset, no hardware needed
    python maze_game.py --board sim --keyboard  arrow keys also move (demo / testing)
    python maze_game.py --windowed              run in a 1280x720 window instead of full screen

Close the OpenBCI GUI first: only one program can use the board at a time.

Keys
  menu      UP/DOWN choose, LEFT/RIGHT change, ENTER select, ESC quit
  playing   SPACE pause/resume, ESC pause menu, X flag the last move as unintended

Everything is saved to data/sub-<subject>/game-<timestamp>/ (see docs/data_format.md).
"""
import argparse

import pygame

from ssvep_maze import settings as S
from ssvep_maze.game import Game
from ssvep_maze.screen import QuitGame


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    hw = p.add_argument_group("headset")
    hw.add_argument("--board", choices=["ganglion", "ganglion-native", "synthetic", "sim"], default="ganglion",
                    help="ganglion = via the BLED112 dongle; sim = simulated headset")
    hw.add_argument("--serial-port", default=None, help="dongle port, e.g. COM3 (found automatically if omitted)")
    hw.add_argument("--mac-address", default=None)
    hw.add_argument("--firmware", choices=["2", "3", "auto"], default="auto")
    hw.add_argument("--channels", default="O1,Oz,O2,Cz", help="names for Ganglion inputs +1..+4, in order")
    hw.add_argument("--freqs", default=",".join(f"{f:g}" for f in S.DEFAULT_FREQUENCIES_HZ),
                    help="flicker frequencies in Hz for up,right,down,left")
    hw.add_argument("--verbose", action="store_true", help="show BrainFlow's connection log")

    start = p.add_argument_group("starting menu values")
    start.add_argument("--subject", default="S01")
    start.add_argument("--size", type=int, default=7, choices=S.MAZE_SIZES)
    start.add_argument("--difficulty", default="Medium", choices=S.DIFFICULTIES)
    start.add_argument("--fog", action="store_true")
    start.add_argument("--guide", action="store_true", help="start with the red guide box on")
    start.add_argument("--speed", default="Normal", choices=S.SPEED_NAMES)
    start.add_argument("--use-saved-calibration", action="store_true", help="start with 'saved profile' selected")

    ctl = p.add_argument_group("decision rule (defaults come from the offline benchmark; change to experiment)")
    ctl.add_argument("--horizon", type=float, default=S.EVIDENCE_MEMORY_S, help="seconds of evidence that count")
    ctl.add_argument("--floor", type=float, default=S.MIN_AVERAGE_SCORE,
                     help="the leading button's average normalised score must reach this")
    ctl.add_argument("--evidence", type=float, default=None, help="override the speed preset's margin")
    ctl.add_argument("--reject-uv", type=float, default=S.ARTIFACT_LIMIT_UV,
                     help="skip windows that swing more than this many microvolts")

    cal = p.add_argument_group("calibration")
    cal.add_argument("--calib-rounds", type=int, default=S.CALIBRATION_ROUNDS, help="times each button is cued")
    cal.add_argument("--calib-cue", type=float, default=S.CALIBRATION_CUE_S, help="seconds of flicker per cue")
    cal.add_argument("--calib-break", type=float, default=S.CALIBRATION_BREAK_S, help="blink break before each cue")
    cal.add_argument("--calib-centre", type=float, default=S.CALIBRATION_MAZE_GAZE_S,
                     help="seconds looking at the maze centre (two blocks)")

    look = p.add_argument_group("layout (fractions of screen height)")
    look.add_argument("--button", type=float, default=S.BUTTON_FRACTION, help="button size")
    look.add_argument("--maze-frac", type=float, default=S.MAZE_FRACTION, help="maze size")
    look.add_argument("--gap", type=float, default=S.VERTICAL_GAP_FRACTION, help="maze to up/down buttons")
    look.add_argument("--side-gap", type=float, default=S.SIDE_GAP_FRACTION, help="maze to left/right buttons")
    look.add_argument("--no-bars", action="store_true", help="hide the evidence bars")
    look.add_argument("--windowed", action="store_true")

    dev = p.add_argument_group("testing")
    dev.add_argument("--keyboard", action="store_true", help="arrow keys also move (logged as keyboard)")
    dev.add_argument("--autostart", action="store_true", help="skip menus and key presses")
    dev.add_argument("--levels", type=int, default=None, help="with --autostart: quit after this many mazes")
    dev.add_argument("--max-seconds", type=float, default=None, help="quit after this long")
    dev.add_argument("--screenshots", default=None, help="folder: save one screenshot of each screen")
    dev.add_argument("--out", default="data", help="where sessions are saved")
    dev.add_argument("--seed", type=int, default=None, help="fix the random mazes and cue order")

    args = p.parse_args(argv)
    args.freqs = [float(f) for f in args.freqs.split(",")]
    if len(args.freqs) != 4:
        p.error("--freqs needs four values (up,right,down,left)")
    args.channels = [c.strip() for c in args.channels.split(",") if c.strip()]
    if args.board == "sim":
        args.channels = ["O1", "Oz", "O2", "Cz"]
    return args


def main():
    try:
        Game(parse_args()).run()
    except QuitGame:
        pygame.quit()


if __name__ == "__main__":
    main()
