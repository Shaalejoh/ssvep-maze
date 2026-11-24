"""
Everything drawn on screen: the window, the maze with its four flickering buttons, and text screens.

The flicker is sinusoidal (brightness follows a sine wave) and tied to the frame count rather
than the clock, so as long as the monitor's vsync works and no frames are dropped, every button
flickers at exactly its frequency, even ones that don't divide the refresh rate evenly.
"""
import math
import time

import numpy as np
import pygame

from .settings import (ACCENT_BLUE, BAD_RED, BLACK, BUTTON_FRACTION, CUE_RED, DIM_GREY, DIRECTIONS, FLOOR, FOG,
                       GOAL, GOLD, GOOD_GREEN, MAZE_FRACTION, PLAYER, SIDE_GAP_FRACTION, TRAIL,
                       VERTICAL_GAP_FRACTION, WALL, WHITE)


class QuitGame(Exception):
    """Raised when the window is closed (or ESC during start-up): the session saves and ends."""


def format_time(seconds):
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


class Screen:
    """The pygame window. Full screen by default; 1280x720 with --windowed."""

    def __init__(self, windowed):
        pygame.init()
        self.vsync = True
        if windowed:
            size, flags = (1280, 720), pygame.SCALED
        else:
            size, flags = pygame.display.get_desktop_sizes()[0], pygame.FULLSCREEN | pygame.SCALED
        try:
            self.surface = pygame.display.set_mode(size, flags, vsync=1)
        except pygame.error:
            self.vsync = False
            self.surface = pygame.display.set_mode(size, flags)
        pygame.display.set_caption("SSVEP Maze")
        pygame.mouse.set_visible(windowed)
        self.w, self.h = self.surface.get_size()
        self.big = pygame.font.SysFont(None, max(40, self.h // 12))
        self.font = pygame.font.SysFont(None, max(26, self.h // 26))
        self.small = pygame.font.SysFont(None, max(20, self.h // 40))
        self.refresh_hz, self.frame_s = self.measure_refresh()

    def measure_refresh(self, frames=120):
        """Flip a blank screen repeatedly and time it. With vsync, each flip waits for the next refresh.
        Without working vsync the game paces frames with the clock at 60 Hz instead."""
        stamps = []
        for _ in range(frames):
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE):
                    raise QuitGame
            self.surface.fill(BLACK)
            pygame.display.flip()
            stamps.append(time.perf_counter())
        median_gap = float(np.median(np.diff(stamps[10:])))
        if not self.vsync or median_gap < 1 / 400:          # flips aren't waiting for the monitor
            self.vsync = False
            return 60.0, 1 / 60.0
        return 1.0 / median_gap, median_gap


def draw_text_screen(screen, title, lines, hint=None, extra=None):
    """A static screen: big title, lines of text (str, or (str, colour)), a hint at the bottom.
    `extra(surface, y)` can draw more below the text (used for the stars)."""
    surf = screen.surface
    surf.fill(BLACK)
    y = int(0.08 * screen.h)
    for t in title:
        img = screen.big.render(t, True, WHITE)
        surf.blit(img, img.get_rect(midtop=(screen.w // 2, y)))
        y += int(screen.big.get_linesize() * 1.1)
    y += int(0.02 * screen.h)
    for line in lines:
        text, colour = (line, WHITE) if isinstance(line, str) else line
        if text:
            img = screen.small.render(text, True, colour)
            surf.blit(img, img.get_rect(midtop=(screen.w // 2, y)))
        y += int(screen.small.get_linesize() * 1.2)
    if extra:
        extra(surf, y)
    if hint:
        img = screen.small.render(hint, True, DIM_GREY)
        surf.blit(img, img.get_rect(midbottom=(screen.w // 2, int(0.95 * screen.h))))
    pygame.display.flip()


def draw_star(surf, centre, radius, filled):
    points = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        r = radius if i % 2 == 0 else radius * 0.45
        points.append((centre[0] + r * math.cos(angle), centre[1] + r * math.sin(angle)))
    if filled:
        pygame.draw.polygon(surf, GOLD, points)
    else:
        pygame.draw.polygon(surf, DIM_GREY, points, max(1, int(radius // 6)))


class MazeView:
    """The play screen: maze in the centre, a button on each side, status text in the side panels."""

    def __init__(self, screen, button=BUTTON_FRACTION, show_bars=True, maze=MAZE_FRACTION,
                 gap=VERTICAL_GAP_FRACTION, side_gap=SIDE_GAP_FRACTION):
        self.s, self.show_bars = screen, show_bars
        self.button_frac, self.maze_frac, self.gap, self.side_gap = button, maze, gap, side_gap
        self.maze, self.maze_image = None, None
        self.hud_left, self.hud_right, self.banner = [], [], None
        self._hud_key = self._banner_key = None

    def layout(self, n):
        """Work out where everything goes for an n x n maze."""
        H, cx, cy = self.s.h, self.s.w // 2, self.s.h // 2
        self.cell = int(self.maze_frac * H) // n
        side = self.cell * n
        self.maze_rect = pygame.Rect(0, 0, side, side)
        self.maze_rect.center = (cx, cy)
        b, gap, sgap = int(self.button_frac * H), int(self.gap * H), int(self.side_gap * H)
        m = self.maze_rect
        centres = {"up": (cx, m.top - gap - b // 2), "down": (cx, m.bottom + gap + b // 2),
                   "left": (m.left - sgap - b // 2, cy), "right": (m.right + sgap + b // 2, cy)}
        self.buttons = {}
        for d, c in centres.items():
            r = pygame.Rect(0, 0, b, b)
            r.center = c
            self.buttons[d] = r
        self.border = max(4, H // 120)
        self.panel_left = pygame.Rect(int(0.02 * self.s.w), int(0.06 * H), self.buttons["left"].left - int(0.04 * self.s.w), H)
        x = self.buttons["right"].right + int(0.02 * self.s.w)
        self.panel_right = pygame.Rect(x, int(0.06 * H), self.s.w - x - int(0.02 * self.s.w), H)

    def render_maze(self, maze, pos, trail, visible=None, bumped=False):
        """Redraw the maze image (only needed when something on it changes). visible=None means no fog."""
        if maze is not self.maze:
            self.maze = maze
            self.layout(maze.n)
        img = pygame.Surface(self.maze_rect.size)
        img.fill(FLOOR)
        c, w = self.cell, max(2, self.cell // 14)
        if visible is not None:
            for cell in maze.passages:
                if cell not in visible:
                    pygame.draw.rect(img, FOG, (cell[0] * c, cell[1] * c, c, c))
        gx, gy = maze.goal
        pygame.draw.rect(img, GOAL, (gx * c + c // 6, gy * c + c // 6, c - c // 3, c - c // 3))
        for (x, y) in trail:
            pygame.draw.circle(img, TRAIL, (x * c + c // 2, y * c + c // 2), max(2, c // 10))
        for (x, y), open_sides in maze.passages.items():
            if visible is not None and (x, y) not in visible:
                continue
            X, Y = x * c, y * c
            if "up" not in open_sides:
                pygame.draw.line(img, WALL, (X, Y), (X + c, Y), w)
            if "down" not in open_sides:
                pygame.draw.line(img, WALL, (X, Y + c), (X + c, Y + c), w)
            if "left" not in open_sides:
                pygame.draw.line(img, WALL, (X, Y), (X, Y + c), w)
            if "right" not in open_sides:
                pygame.draw.line(img, WALL, (X + c, Y), (X + c, Y + c), w)
        pygame.draw.rect(img, WALL, img.get_rect(), w)
        px, py = pos
        pygame.draw.circle(img, BAD_RED if bumped else PLAYER, (px * c + c // 2, py * c + c // 2), c // 3)
        self.maze_image = img

    def set_hud(self, left, right=()):
        """Side-panel text. Lines are str or (str, colour). Only re-rendered when it changes."""
        key = (tuple(left), tuple(right))
        if key != self._hud_key:
            self._hud_key = key
            self.hud_left = [self._render(t) for t in left]
            self.hud_right = [self._render(t) for t in right]

    def _render(self, item):
        text, colour = (item, WHITE) if isinstance(item, str) else item
        return self.s.small.render(text, True, colour) if text else None

    def set_banner(self, text, colour=WHITE):
        """A message box over the middle of the maze, or None to clear it."""
        if (text, colour) != self._banner_key:
            self._banner_key = (text, colour)
            self.banner = self.s.font.render(text, True, colour) if text else None

    def draw(self, brightness, cue=None, progress=None):
        """Draw one frame and flip. brightness: {direction: 0..1}. Returns the time of the flip."""
        surf = self.s.surface
        surf.fill(BLACK)
        if self.maze_image is not None:
            surf.blit(self.maze_image, self.maze_rect)
        for d, rect in self.buttons.items():
            g = int(round(255 * min(1.0, max(0.0, brightness.get(d, 0.5)))))
            pygame.draw.rect(surf, (g, g, g), rect)
        if cue in self.buttons:                                   # the red guide / calibration box
            pygame.draw.rect(surf, CUE_RED, self.buttons[cue].inflate(2 * self.border + 6, 2 * self.border + 6), self.border)
        if self.show_bars and progress is not None:
            self._draw_progress_bars(surf, *progress)
        for panel, lines in ((self.panel_left, self.hud_left), (self.panel_right, self.hud_right)):
            y = panel.top
            for img in lines:
                if img is not None:
                    surf.blit(img, (panel.left, y))
                y += int(self.s.small.get_linesize() * 1.15)
        if self.banner:
            r = self.banner.get_rect(center=self.maze_rect.center)
            pygame.draw.rect(surf, BLACK, r.inflate(30, 18))
            pygame.draw.rect(surf, DIM_GREY, r.inflate(30, 18), 2)
            surf.blit(self.banner, r)
        pygame.display.flip()
        return time.perf_counter()

    def _draw_progress_bars(self, surf, fraction, strong):
        """A bar between the maze and the leading button fills up as its evidence builds.
        Green when the button is also strong enough to trigger a move, grey otherwise."""
        m = self.maze_rect
        for i, d in enumerate(DIRECTIONS):
            if fraction[i] <= 0:
                continue
            r, colour = self.buttons[d], (GOOD_GREEN if strong[i] else DIM_GREY)
            if d == "up":
                pygame.draw.rect(surf, colour, (r.left, m.top - 11, int(r.w * fraction[i]), 6))
            elif d == "down":
                pygame.draw.rect(surf, colour, (r.left, m.bottom + 5, int(r.w * fraction[i]), 6))
            elif d == "left":
                pygame.draw.rect(surf, colour, (m.left - 11, r.bottom - int(r.h * fraction[i]), 6, int(r.h * fraction[i])))
            else:
                pygame.draw.rect(surf, colour, (m.right + 5, r.bottom - int(r.h * fraction[i]), 6, int(r.h * fraction[i])))


def menu_colours(selected):
    return (ACCENT_BLUE if selected else WHITE), (ACCENT_BLUE if selected else (190, 190, 190))
