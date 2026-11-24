# Hardware and setup

## What I used

| Part | Notes |
|---|---|
| OpenBCI Ganglion | 4-channel EEG board, 200 samples/s, with the BLED112 Bluetooth dongle (borrowed from Synaptech) |
| Ultracortex Mark IV headset (Medium) | I printed it from [OpenBCI's design files](https://github.com/OpenBCI/Ultracortex) |
| Dry spiky electrodes | in the headset at O1, Oz and O2 (Cz is optional, the game doesn't use it) |
| 2 ear-clip electrodes | REF and D_G (ground/bias) |
| Monitor | 120 Hz with vsync on. Other refresh rates work too, it just has to be steady |
| PC | Windows |

## Where the electrodes go

The game only uses the electrodes over the visual cortex, at the back of your head. Name the
Ganglion's inputs after where they actually are, because the game looks for channels named O1, Oz, O2,
POz, PO3, PO4, PO7, PO8 or Pz:

| Ganglion input | Electrode |
|---|---|
| +1 | O1 (back of the head, left of center) |
| +2 | Oz (back of the head, center) |
| +3 | O2 (back of the head, right of center) |
| +4 | Cz (top of the head), optional |
| REF | ear clip |
| D_G | ear clip on the other ear |

The electrodes plug into +1 to +4 with all four of the Ganglion's switches down, so every channel uses
the ear reference (OpenBCI's Ganglion guide shows the pin layout). I left Cz unplugged for most of my
sessions because it was more comfortable, and it doesn't matter for the game. The start screen shows
which channels it's going to use, so check that it lists O1, Oz and O2 before you start.

## Getting good contact

Check the impedance in the OpenBCI GUI before every session and fix anything that looks off. My
sessions worked with O1, Oz and O2 anywhere from about 13 to 48 kΩ, but lower and more even across the
channels is better. If you have a lot of hair, work each electrode through it until it's touching
skin. The reference clip affects every channel, so make sure it's sitting flat on your earlobe.

The spiky electrodes start to hurt after a while, and the contact can change if the headset shifts, so
shorter sessions with breaks are easier and the data is more consistent.

Recalibrate any time you take the headset off and put it back on. Within one sitting my signal was
pretty stable (two calibrations 23 minutes apart were within 0.05 of each other), but between sittings
the "looking" level changed by as much as 0.26, and using one session's calibration on another session
dropped accuracy from 99.5% to 93%.

## Connecting

Close the OpenBCI GUI first, since only one program can connect to the board at a time.

```bash
python maze_game.py                       # finds the dongle automatically
python maze_game.py --serial-port COM3    # or tell it the port
python maze_game.py --verbose             # show BrainFlow's connection log
```

One thing that took me a while to figure out: with the BLED112 dongle and no MAC address, BrainFlow
never opens the board (error 13) unless the firmware option is set to "auto". The game always uses
`fw:auto` for that, so you shouldn't run into it, but it's good to know if you write your own scripts.

If it won't connect, check that the board is on and charged, it's close to the dongle, and the GUI is
closed. After that, try turning the board off and on and unplugging and replugging the dongle.

## Monitor

Run it full screen (the default) so vsync works. The start screen shows the refresh rate it measured
and warns you if vsync isn't working. Without vsync the frames get timed by the clock, which isn't as
precise.
