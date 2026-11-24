"""SSVEP Maze: steer through a maze by looking at flickering buttons, read from EEG.

Modules, roughly in the order data flows through them:
    headset            EEG in: the Ganglion (via BrainFlow) or a simulated headset; recording to disk
    signal_processing  filtering and CCA scores: how strongly is each button's flicker present?
    decision           calibration reference levels and the evidence accumulator that decides to move
    maze               maze generation and pathfinding
    screen             drawing the maze, buttons and text
    calibration        the ~70 s calibration routine
    level              playing one maze
    game               the session: menus, frame loop, saving
    settings           every tunable number in one place
"""
