"""
Maze generation and pathfinding. Pure Python, no graphics.

A maze is a grid of cells. `passages[(x, y)]` is the set of directions you can move from that
cell (an open side has no wall). Every maze is fully connected, so the goal is always reachable.

Difficulty (averages on a 7x7 maze: shortest route / turns on it / dead ends):
    Easy    18 / 9 / 3   recursive backtracker, then 40% of dead ends opened into loops
    Medium  25 / 15 / 5  recursive backtracker, goal in the far corner
    Hard    28 / 17 / 7  growing tree (long corridors with side branches), goal at the farthest cell
Harder mazes need more moves, more changes of direction (each one means moving your eyes to a
different button), and offer more wrong turns to explore in free play.
"""
import collections
import random

from .settings import DIRECTIONS, OPPOSITE, STEP


def neighbours(n, x, y):
    """(direction, cell) for every cell next to (x, y) inside an n x n grid."""
    for d, (dx, dy) in STEP.items():
        if 0 <= x + dx < n and 0 <= y + dy < n:
            yield d, (x + dx, y + dy)


def step(cell, direction):
    dx, dy = STEP[direction]
    return cell[0] + dx, cell[1] + dy


class Maze:
    def __init__(self, n, difficulty, seed):
        """The same (n, difficulty, seed) always gives the same maze, which is how "replay" works."""
        self.n, self.difficulty, self.seed = n, difficulty, seed
        rng = random.Random(seed)
        self.passages = {(x, y): set() for x in range(n) for y in range(n)}
        if difficulty == "Hard":
            self._growing_tree(rng, chance_of_side_branch=0.1)
        else:
            self._recursive_backtracker(rng)
        if difficulty == "Easy":
            self._add_loops(rng, fraction_of_dead_ends=0.4)

        self.start = (0, 0)
        if difficulty == "Hard":
            from_start = self.distances_from(self.start)
            farthest = max(from_start.values())
            self.goal = rng.choice(sorted(c for c, d in from_start.items() if d == farthest))
        else:
            self.goal = (n - 1, n - 1)
        self.moves_to_goal = self.distances_from(self.goal)       # shortest number of moves from every cell
        self.shortest_route = self.moves_to_goal[self.start]
        self.dead_ends = sum(len(p) == 1 for c, p in self.passages.items() if c not in (self.start, self.goal))

    # ------------------------------------------------------------------ generation
    def _open(self, cell, direction):
        """Knock down the wall between a cell and its neighbour."""
        other = step(cell, direction)
        self.passages[cell].add(direction)
        self.passages[other].add(OPPOSITE[direction])
        return other

    def _recursive_backtracker(self, rng):
        """Wander to random unvisited neighbours; back up when stuck. Makes long winding corridors."""
        path, visited = [(0, 0)], {(0, 0)}
        while path:
            cell = path[-1]
            options = [(d, nb) for d, nb in neighbours(self.n, *cell) if nb not in visited]
            if not options:
                path.pop()
                continue
            d, nb = rng.choice(options)
            self._open(cell, d)
            visited.add(nb)
            path.append(nb)

    def _growing_tree(self, rng, chance_of_side_branch):
        """Like the backtracker, but sometimes grows from a random earlier cell, which adds side branches."""
        active, visited = [(0, 0)], {(0, 0)}
        while active:
            if rng.random() >= chance_of_side_branch:
                i = len(active) - 1
            else:
                i = rng.randrange(len(active))
            cell = active[i]
            options = [(d, nb) for d, nb in neighbours(self.n, *cell) if nb not in visited]
            if not options:
                active.pop(i)
                continue
            d, nb = rng.choice(options)
            self._open(cell, d)
            visited.add(nb)
            active.append(nb)

    def _add_loops(self, rng, fraction_of_dead_ends):
        """Open one extra wall in some dead ends, creating loops (more than one way to the goal)."""
        dead_ends = [c for c, p in self.passages.items() if len(p) == 1]
        rng.shuffle(dead_ends)
        for cell in dead_ends[:int(round(fraction_of_dead_ends * len(dead_ends)))]:
            if len(self.passages[cell]) != 1:
                continue
            walls = [d for d, nb in neighbours(self.n, *cell) if d not in self.passages[cell]]
            if walls:
                self._open(cell, rng.choice(walls))

    # ------------------------------------------------------------------ questions about the maze
    def distances_from(self, source):
        """Breadth-first search: number of moves from `source` to every cell."""
        dist, queue = {source: 0}, collections.deque([source])
        while queue:
            cell = queue.popleft()
            for d in self.passages[cell]:
                nb = step(cell, d)
                if nb not in dist:
                    dist[nb] = dist[cell] + 1
                    queue.append(nb)
        return dist

    def is_open(self, cell, direction):
        return direction in self.passages[cell]

    def best_direction(self, cell):
        """A direction on a shortest route to the goal (checked in a fixed order, so the guide box never
        jumps between two equally good choices)."""
        for d in DIRECTIONS:
            if d in self.passages[cell] and self.moves_to_goal[step(cell, d)] < self.moves_to_goal[cell]:
                return d
        return None

    def gets_closer(self, cell, direction):
        """True if moving that way is open and shortens the route to the goal."""
        return direction in self.passages[cell] and self.moves_to_goal[step(cell, direction)] < self.moves_to_goal[cell]

    def visible_from(self, cell, depth):
        """Cells you can see with fog of war: up to `depth` steps along open corridors."""
        seen, frontier = {cell}, [cell]
        for _ in range(depth):
            nxt = []
            for c in frontier:
                for d in self.passages[c]:
                    nb = step(c, d)
                    if nb not in seen:
                        seen.add(nb)
                        nxt.append(nb)
            frontier = nxt
        return seen
