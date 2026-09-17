"""Unit tests for the playability solver."""

from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import solver  # noqa: E402
import vision  # noqa: E402


def make_grid(rows: int = 7, cols: int = 7, pad: int = 1) -> vision.Grid:
    return vision.Grid(x0=0, y0=0, cell_w=10, cell_h=10, cols=cols, rows=rows, pad=pad)


def make_occupancy(
    rows: int = 7,
    cols: int = 7,
    occupied: tuple[tuple[int, int], ...] = (),
    unknown: tuple[tuple[int, int], ...] = (),
) -> np.ndarray:
    occupancy = np.full((rows, cols), vision.EMPTY, dtype=np.uint8)
    for row, col in occupied:
        occupancy[row, col] = vision.OCCUPIED
    for row, col in unknown:
        occupancy[row, col] = vision.UNKNOWN
    return occupancy


class TestSolver(unittest.TestCase):
    def test_clear_path_to_border(self) -> None:
        grid = make_grid()
        occupancy = make_occupancy()
        self.assertTrue(solver.is_playable(grid, occupancy, 3, 1, "R"))

    def test_blocked_by_another_arrow(self) -> None:
        grid = make_grid()
        occupancy = make_occupancy(occupied=((3, 4),))
        self.assertFalse(solver.is_playable(grid, occupancy, 3, 1, "R"))

    def test_unknown_cell_blocks(self) -> None:
        grid = make_grid()
        occupancy = make_occupancy(unknown=((3, 3),))
        self.assertFalse(solver.is_playable(grid, occupancy, 3, 1, "R"))

    def test_padding_does_not_block(self) -> None:
        grid = make_grid()
        occupancy = make_occupancy(occupied=((3, 0), (0, 3), (3, 6), (6, 3)))
        self.assertTrue(solver.is_playable(grid, occupancy, 3, 1, "L"))
        self.assertTrue(solver.is_playable(grid, occupancy, 3, 1, "R"))
        self.assertTrue(solver.is_playable(grid, occupancy, 1, 3, "U"))
        self.assertTrue(solver.is_playable(grid, occupancy, 5, 3, "D"))

    def test_head_at_board_edge(self) -> None:
        grid = make_grid()
        occupancy = make_occupancy()
        self.assertTrue(solver.is_playable(grid, occupancy, 1, 1, "L"))
        self.assertTrue(solver.is_playable(grid, occupancy, 1, 1, "U"))

    def test_playable_moves_sorted(self) -> None:
        grid = make_grid()
        occupancy = make_occupancy(occupied=((3, 4),))
        heads = {(3, 1): "R", (1, 1): "U", (2, 2): "L"}
        moves = solver.playable_moves(grid, occupancy, heads)
        self.assertEqual(moves, [(1, 1, "U"), (2, 2, "L")])


if __name__ == "__main__":
    unittest.main()
