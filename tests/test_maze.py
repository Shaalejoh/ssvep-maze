import statistics

import pytest

from ssvep_maze.maze import Maze, step
from ssvep_maze.settings import DIFFICULTIES, MAZE_SIZES


@pytest.mark.parametrize("size", MAZE_SIZES)
@pytest.mark.parametrize("difficulty", DIFFICULTIES)
def test_every_cell_reachable_and_walls_consistent(size, difficulty):
    for seed in range(20):
        m = Maze(size, difficulty, seed)
        assert len(m.distances_from(m.start)) == size * size           # fully connected
        for cell, open_sides in m.passages.items():
            for d in open_sides:                                         # a wall is open from both sides
                other = step(cell, d)
                assert 0 <= other[0] < size and 0 <= other[1] < size
                assert m.passages[other] & {"up", "down", "left", "right"}


def test_perfect_mazes_have_exactly_one_route():
    # A perfect maze is a tree: n*n cells joined by n*n - 1 passages.
    for difficulty in ("Medium", "Hard"):
        for seed in range(20):
            m = Maze(9, difficulty, seed)
            passages = sum(len(p) for p in m.passages.values()) // 2
            assert passages == 9 * 9 - 1


def test_easy_mazes_have_loops():
    m = Maze(9, "Easy", 3)
    assert sum(len(p) for p in m.passages.values()) // 2 > 9 * 9 - 1


def test_same_seed_same_maze():
    a, b = Maze(11, "Hard", 42), Maze(11, "Hard", 42)
    assert a.passages == b.passages and a.goal == b.goal


def test_following_best_direction_reaches_goal_in_shortest_route():
    for difficulty in DIFFICULTIES:
        m = Maze(9, difficulty, 7)
        pos, moves = m.start, 0
        while pos != m.goal:
            pos = step(pos, m.best_direction(pos))
            moves += 1
        assert moves == m.shortest_route


def test_difficulty_increases_route_length_on_average():
    route = {d: statistics.mean(Maze(7, d, s).shortest_route for s in range(100)) for d in DIFFICULTIES}
    assert route["Easy"] < route["Medium"] < route["Hard"]
