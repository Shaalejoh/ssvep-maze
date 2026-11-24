# Saved data

Every time you run the game it saves a session to `data/sub-<subject>/game-<YYYYMMDD-HHMMSS>/`. It
records the EEG the whole time, menus included, so nothing gets missed if you want to go back and look
at something later. This is the reference for what's in each file.

| File | Contents |
|---|---|
| `raw.csv` | every sample: sample number, package number, each channel in µV, accelerometer, unix time, marker |
| `events.csv` | every event (calibration cues, moves, pauses...) with its exact `sample` in raw.csv |
| `decisions.csv` | one row per move: what was chosen, how long it took, and the scores behind it |
| `meta.json` | settings, channels, decision-rule parameters, calibration results, per-maze statistics |
| `summary.txt` | the summary shown at the end of the session |

Across sessions, in `data/sub-<subject>/`:

| File | Contents |
|---|---|
| `calibration.json` | the latest calibration, reusable from the menu |
| `maze_records.json` | personal bests for each size/difficulty/fog/guide combination |
| `profile_history.jsonl` | one line per calibration and per maze, across all days |

## decisions.csv columns

| Column | Meaning |
|---|---|
| `t_s` | seconds since the game started |
| `level`, `size`, `difficulty`, `fog`, `guide`, `speed` | the maze and settings |
| `A` | evidence margin used (from the speed setting) |
| `source` | `bci` (headset) or `keyboard` (testing) |
| `target` | the guide box direction (guided play only) |
| `predicted` | the direction chosen |
| `outcome` | `move` or `bump` (into a wall) |
| `toward_goal` | 1 if the move shortened the route to the goal |
| `correct` | guided play: 1 if it matched a shortest route |
| `decision_time_s` | time since the previous move (or the start of the maze) |
| `evidence_lead`, `evidence_margin`, `leader_mean` | the leading button's total, its lead, and its average |
| `norm_scores_up_right_down_left` | rescaled scores at the moment of decision (0 = not looking, 1 = looking) |
| `raw_cca_up_right_down_left` | raw CCA scores at that moment |
| `window_s`, `dead_s` | window length and pause after a move |

## Marker codes (raw.csv `marker` column)

| Code | Event |
|---|---|
| 61–64 | move shown: up, right, down, left |
| 71–74 | bumped into a wall: up, right, down, left |
| 80 | calibration: look at the maze centre |
| 81–84 | calibration cue: up, right, down, left |
| 89 | calibration blink break |
| 90 | first maze of the session |
| 91 | maze complete |
| 92 / 93 | pause / resume |
| 94 | maze start (shown, flicker on) |
| 95 | maze abandoned |
| 96 | X pressed: "the last move wasn't what I meant" |
