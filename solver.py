"""Playability solver for the Auto-ARROWS bot.

An arrow is playable when the straight line from its head to the board border,
in the head direction, is free of other arrows. With only heads and directions,
the board is represented by an occupancy grid (see `vision.build_occupancy`).
"""

from __future__ import annotations

import numpy as np

import vision

_DIRECTIONS = {"U": (-1, 0), "D": (1, 0), "L": (0, -1), "R": (0, 1)}


def is_playable(
    grid: vision.Grid,
    occupancy: np.ndarray,
    row: int,
    col: int,
    direction: str,
) -> bool:
    """Whether the arrow whose head is at (row, col) can exit the board.

    The scan starts in front of the head and stops when it leaves the real board
    (padding cells are exit space). An occupied cell blocks the arrow; an unknown
    cell (off screen) also blocks it, to stay safe under the penalty rule.
    """
    if not grid.inside_board(row, col) or occupancy[row, col] == vision.UNKNOWN:
        return False
    dr, dc = _DIRECTIONS[direction]
    r, c = row + dr, col + dc
    while grid.inside_board(r, c):
        value = occupancy[r, c]
        if value == vision.UNKNOWN or value == vision.OCCUPIED:
            return False
        r += dr
        c += dc
    return True


def playable_moves(
    grid: vision.Grid,
    occupancy: np.ndarray,
    heads: dict[tuple[int, int], str],
) -> list[tuple[int, int, str]]:
    """Return the list of playable (row, col, direction) sorted by reading order."""
    moves = [
        (row, col, direction)
        for (row, col), direction in heads.items()
        if is_playable(grid, occupancy, row, col, direction)
    ]
    return sorted(moves)
