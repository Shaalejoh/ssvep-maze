"""
Every tunable number in the game, in one place.

Most of these were chosen by replaying recorded sessions offline and measuring speed against
accuracy (see docs/results.md). Change them here rather than inside the code.
"""

# ----------------------------------------------------------------------------- the four buttons
DIRECTIONS = ["up", "right", "down", "left"]

# How a direction moves you on the maze grid: (column change, row change). Row 0 is the top.
STEP = {"up": (0, -1), "right": (1, 0), "down": (0, 1), "left": (-1, 0)}
OPPOSITE = {"up": "down", "down": "up", "left": "right", "right": "left"}

# Flicker frequency of each button, in Hz. They sit above the 8-12 Hz alpha rhythm (which would
# otherwise be mistaken for a button) and are spaced 2 Hz apart so their responses don't overlap.
DEFAULT_FREQUENCIES_HZ = [11.0, 13.0, 15.0, 17.0]   # up, right, down, left

# EEG channels that sit over the visual cortex. Only channels with one of these names are used to
# detect the flicker response; anything else (for example Cz) is recorded but ignored.
VISUAL_CHANNEL_NAMES = ("O1", "Oz", "O2", "POz", "PO3", "PO4", "PO7", "PO8", "Pz")

# ----------------------------------------------------------------------------- signal processing
BANDPASS_HZ = (6.0, 45.0)        # keep the flicker responses and their harmonics, drop drift and muscle noise
MAINS_HZ = 60.0                  # North American power-line hum, removed with a notch filter
REFERENCE_HARMONICS = 3          # CCA compares the EEG against sine waves at f, 2f and 3f
ARTIFACT_LIMIT_UV = 300.0        # skip a window if any channel swings more than this (blink, movement, lost contact)
BUFFER_SECONDS = 12.0            # how much filtered EEG to keep in memory
SCORE_INTERVAL_S = 0.1           # score the latest EEG window 10 times per second

# ----------------------------------------------------------------------------- deciding when to move
# Each tick, every button gets a "normalised score": 0 means as strong as it typically is when you
# are NOT looking at it, 1 means as strong as when you ARE (both measured during calibration).
# Those scores are summed over a short memory. A move happens when the leading button's sum beats
# the runner-up's by `margin`, and the leader's average is at least MIN_AVERAGE_SCORE.
EVIDENCE_MEMORY_S = 3.0          # only the last 3 s of scores count, so old evidence fades out
MIN_AVERAGE_SCORE = 0.4          # stops slow drifts while you study the maze from adding up to a move
SCORE_CLIP = (-1.0, 2.5)         # one extreme window can't dominate the sum


class SpeedPreset:
    """A speed setting.

    window_s: how much EEG (seconds) each score looks at. Longer = steadier but slower to react.
    pause_s:  after each move, scores are ignored this long while your eyes travel to the next button.
    margin:   how far the leading button's evidence must beat the runner-up's before moving.
    """

    def __init__(self, window_s, pause_s, margin, description):
        self.window_s, self.pause_s, self.margin, self.description = window_s, pause_s, margin, description

    def to_log(self):
        """The format used in meta.json since the first version, kept so old and new logs compare."""
        return {"window": self.window_s, "dead": self.pause_s, "A": self.margin}


SPEED_PRESETS = {
    "Careful": SpeedPreset(1.25, 1.25, 5.0, "most margin, slowest"),
    "Normal": SpeedPreset(1.25, 1.25, 3.0, "default"),
    "Fast": SpeedPreset(1.25, 1.25, 2.0, "lower evidence bar"),
    "Turbo": SpeedPreset(1.0, 1.0, 3.0, "1 s window and pause; your call, watch for warnings"),
}
SPEED_NAMES = list(SPEED_PRESETS)
WINDOW_LENGTHS_S = sorted({p.window_s for p in SPEED_PRESETS.values()})   # calibration measures each of these

# The in-game warning for Fast/Turbo: this many guided errors or X-flags within the last N moves.
WARN_AFTER_PROBLEMS, WARN_WINDOW_MOVES = 2, 20

# ----------------------------------------------------------------------------- calibration
CALIBRATION_ROUNDS = 3           # each button is cued this many times, in a shuffled order each round
CALIBRATION_CUE_S = 3.0          # seconds of flicker per cue
CALIBRATION_BREAK_S = 1.5        # flicker off before each cue so you can blink; the red box already shows the target
CALIBRATION_MAZE_GAZE_S = 12.0   # total time looking at the maze centre (split into two blocks)
SETTLE_S = 0.35                  # the brain response takes about this long to build after the flicker starts
WEAK_BUTTON_SEPARATION = 0.15    # warn if a button's looking/not-looking difference is smaller than this
LOW_CUE_FRACTION = 0.6           # flag a single cue that came out below this fraction of the button's usual level

# ----------------------------------------------------------------------------- maze and layout
MAZE_SIZES = [5, 6, 7, 9, 11, 13]
DIFFICULTIES = ["Easy", "Medium", "Hard"]
DIFFICULTY_TEXT = {"Easy": "loops, few dead ends", "Medium": "one route, corner goal",
                   "Hard": "longer route, more dead ends, goal at the farthest cell"}
FOG_SIGHT_STEPS = 3              # with fog of war, you can see this many steps along open corridors

# Sizes as fractions of the screen height. The gaps keep the flicker out of near-peripheral
# vision while you study the edges of the maze (closer buttons caused false moves).
MAZE_FRACTION = 0.44
BUTTON_FRACTION = 0.17
VERTICAL_GAP_FRACTION = 0.085    # maze to up/down buttons
SIDE_GAP_FRACTION = 0.12         # maze to left/right buttons (there is more room at the sides)

# ----------------------------------------------------------------------------- event markers
# Numbers written into the EEG recording at the exact sample each event happened.
MARKER = {
    "move": 61,               # 61-64: a move was shown (up, right, down, left)
    "bump": 71,               # 71-74: tried to move into a wall (up, right, down, left)
    "calibration_maze": 80,   # calibration: look at the maze centre
    "calibration_cue": 81,    # 81-84: calibration cue on a button (up, right, down, left)
    "calibration_break": 89,  # calibration: blink break
    "session_start": 90,      # first maze of the session
    "level_complete": 91,
    "pause": 92,
    "resume": 93,
    "level_start": 94,        # maze shown, flicker on
    "level_abandoned": 95,
    "flag_unintended": 96,    # you pressed X: "the last move wasn't what I meant"
}

# ----------------------------------------------------------------------------- colours (R, G, B)
BLACK, WHITE = (0, 0, 0), (235, 235, 235)
CUE_RED, GOOD_GREEN, BAD_RED, DIM_GREY = (230, 40, 40), (70, 200, 110), (235, 70, 60), (120, 120, 120)
WALL, FLOOR, PLAYER, GOAL, TRAIL, FOG = (205, 205, 205), (28, 28, 34), (80, 160, 255), (60, 190, 90), (70, 90, 130), (8, 8, 11)
GOLD, ACCENT_BLUE = (240, 200, 70), (110, 170, 255)

PHOTOSENSITIVITY_WARNING = [
    "WARNING: this game shows flickering light.",
    "Do not continue if you have photosensitive epilepsy",
    "or have ever had a seizure from flashing lights.",
]
