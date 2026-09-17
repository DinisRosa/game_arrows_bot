"""Auto-ARROWS bot loop: observe, decide and tap until the level is cleared.

Usage:
    ./venv/bin/python bot.py                 # play the current level
    ./venv/bin/python bot.py --all           # play level after level
    ./venv/bin/python bot.py --simulate      # only list playable moves
"""

from __future__ import annotations

import argparse
import os
import time

import cv2
import numpy as np

import adb_client
import capture
import solver
import stitch
import vision


def save_debug(
    path: str,
    frame: np.ndarray,
    grid: vision.Grid,
    heads: dict,
    occupancy: np.ndarray | None = None,
    move: tuple[int, int, str] | None = None,
) -> None:
    """Save an annotated frame (grid, heads, occupancy and the chosen move)."""
    canvas = vision.draw_heads(vision.draw_grid(frame, grid), grid, heads)
    if occupancy is not None:
        canvas = vision.draw_occupancy(canvas, grid, occupancy)
    if move is not None:
        row, col, _ = move
        x, y = grid.cell_center(row, col)
        cv2.circle(canvas, (x, y), 22, (0, 0, 255), 4)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    cv2.imwrite(path, canvas)


def detect_board(frame: np.ndarray) -> vision.Grid | None:
    """Detect the board grid (prefers dots, falls back to the arrows themselves)."""
    try:
        grid, _method = vision.detect_grid(frame)
        return grid
    except ValueError:
        return None


def wait_for_stable(
    interval: float = 0.25,
    timeout: float = 6.0,
) -> np.ndarray:
    """Capture frames until two consecutive ones are nearly identical."""
    previous = capture.get_frame()
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(interval)
        frame = capture.get_frame()
        if frame.shape == previous.shape:
            diff = float(np.abs(frame.astype(np.int16) - previous.astype(np.int16)).mean())
            if diff < 1.0:
                return frame
        previous = frame
    return previous


def detect_stable_grid(attempts: int = 4) -> tuple[np.ndarray, vision.Grid] | None:
    """Wait for a stable frame and detect a grid that actually contains arrows."""
    for _ in range(attempts):
        frame = wait_for_stable()
        grid = detect_board(frame)
        if grid is not None and vision.detect_arrowheads(frame, grid):
            return frame, grid
    return None


def wait_for_board_with_arrows() -> tuple[np.ndarray, vision.Grid] | None:
    """Poll until a stable board containing at least one arrow is visible."""
    while True:
        detected = detect_stable_grid()
        if detected is not None:
            frame, grid = detected
            if vision.detect_arrowheads(frame, grid):
                return detected
        time.sleep(2.0)


def play_level(frame: np.ndarray, grid: vision.Grid, args: argparse.Namespace) -> str:
    """Play the current level. Returns 'completed', 'no_moves', 'stuck' or 'max_moves'."""
    failures = 0
    for move_number in range(1, args.max_moves + 1):
        heads = vision.detect_arrowheads(frame, grid)
        if not heads:
            return "completed"
        occupancy = vision.build_occupancy(frame, grid)
        moves = [
            move
            for move in solver.playable_moves(grid, occupancy, heads)
            if grid.inside_board(move[0], move[1])
        ]
        if not moves:
            return "no_moves"

        row, col, direction = moves[0]
        x, y = grid.cell_center(row, col)
        print(f"  move {move_number}: ({row},{col}) {direction} -> tap ({x},{y})")
        save_debug(
            os.path.join(args.outdir, f"move_{move_number:03d}_before.png"),
            frame,
            grid,
            heads,
            occupancy,
            (row, col, direction),
        )
        adb_client.tap(x, y)
        time.sleep(args.delay)

        for _ in range(6):
            frame = capture.get_frame()
            new_occupancy = vision.build_occupancy(frame, grid)
            if new_occupancy[row, col] == vision.EMPTY:
                failures = 0
                break
            time.sleep(0.25)
        else:
            failures += 1
            print(f"    arrow did not leave ({failures})")
            if failures >= 2:
                return "stuck"
        save_debug(
            os.path.join(args.outdir, f"move_{move_number:03d}_after.png"),
            frame,
            grid,
            vision.detect_arrowheads(frame, grid),
            vision.build_occupancy(frame, grid),
        )
    return "max_moves"


def board_is_cut(frame: np.ndarray) -> bool:
    """Whether arrow content reaches any edge of the board viewport."""
    mask = vision.foreground_mask(frame)
    height = mask.shape[0]
    band = mask[stitch.TOP_MARGIN : height - stitch.BOTTOM_MARGIN, :]
    if band.size == 0:
        return False
    return bool(
        band[:, :8].any()
        or band[:, -8:].any()
        or band[:8, :].any()
        or band[-8:, :].any()
    )


def update_global(
    grid: vision.Grid,
    occupancy: np.ndarray,
    heads: dict,
    frame: np.ndarray,
    offset: tuple[float, float],
) -> None:
    """Refresh the global occupancy and heads from the currently visible region."""
    ox, oy = offset
    mask = vision.foreground_mask(frame)
    height, width = mask.shape[:2]
    top = stitch.TOP_MARGIN
    bottom = height - stitch.BOTTOM_MARGIN
    visible: set[tuple[int, int]] = set()
    for row in range(grid.rows):
        for col in range(grid.cols):
            gx, gy = grid.cell_center(row, col)
            x0 = int(round(gx - ox - grid.cell_w / 2))
            x1 = int(round(gx - ox + grid.cell_w / 2))
            y0 = int(round(gy - oy - grid.cell_h / 2))
            y1 = int(round(gy - oy + grid.cell_h / 2))
            if x0 < 0 or x1 > width or y0 < top or y1 > bottom:
                continue
            visible.add((row, col))
            ratio = float(mask[y0:y1, x0:x1].mean())
            occupancy[row, col] = vision.OCCUPIED if ratio >= 0.08 else vision.EMPTY
    for key in list(heads):
        if key in visible:
            del heads[key]
    for x, y, direction in vision.detect_head_pixels(frame, grid.cell_w):
        row = int(round((y + oy - grid.y0) / grid.cell_h))
        col = int(round((x + ox - grid.x0) / grid.cell_w))
        if 0 <= row < grid.rows and 0 <= col < grid.cols and (row, col) in visible:
            heads[(row, col)] = direction


def ensure_visible(
    adb_client,
    grid: vision.Grid,
    row: int,
    col: int,
    offset: tuple[float, float],
    frame: np.ndarray,
    margin: int = 160,
    max_step: int = 350,
) -> tuple[tuple[float, float], np.ndarray]:
    """Pan (one axis at a time) until the given global cell is comfortably inside."""
    width, height = adb_client.screen_size()
    cx, cy = width // 2, height // 2
    for _ in range(14):
        gx, gy = grid.cell_center(row, col)
        sx, sy = gx - offset[0], gy - offset[1]
        if margin <= sx <= width - margin and margin <= sy <= height - margin:
            return offset, frame
        if abs(sx - cx) >= abs(sy - cy):
            delta = max(-max_step, min(max_step, sx - cx))
            if delta == 0:
                return offset, frame
            adb_client.swipe(cx + delta // 2, cy, cx - delta // 2, cy, 300)
            sample = "R" if delta > 0 else "L"
        else:
            delta = max(-max_step, min(max_step, sy - cy))
            if delta == 0:
                return offset, frame
            adb_client.swipe(cx, cy + delta // 2, cx, cy - delta // 2, 300)
            sample = "D" if delta > 0 else "U"
        time.sleep(0.5)
        new_frame = capture.get_frame()
        mx, my, score = stitch._shift(frame, new_frame, sample)
        if score < 0.5:
            print(f"    pan alignment unreliable (score {score:.2f}) - stopping")
            return offset, new_frame
        offset = (offset[0] - mx, offset[1] - my)
        frame = new_frame
    return offset, frame


def play_cut_level(frame: np.ndarray, args: argparse.Namespace) -> str:
    """Stitch a board larger than the screen and play it on the global model."""
    grid, occupancy, heads, offset = stitch.stitch(adb_client, outdir=args.outdir)
    print(f"  stitched board {grid.cols}x{grid.rows}, heads {len(heads)}, offset=({offset[0]:.0f},{offset[1]:.0f})")
    frame = capture.get_frame()
    failures = 0
    for move_number in range(1, args.max_moves + 1):
        if not heads:
            return "completed"
        moves = solver.playable_moves(grid, occupancy, heads)
        if not moves:
            return "no_moves"
        row, col, direction = moves[0]
        offset, frame = ensure_visible(adb_client, grid, row, col, offset, frame)
        gx, gy = grid.cell_center(row, col)
        sx, sy = int(gx - offset[0]), int(gy - offset[1])
        width, height = adb_client.screen_size()
        if not (0 <= sx < width and 0 <= sy < height):
            print("  target still off screen - stopping")
            return "stuck"
        # use the locally detected head (ground truth) nearest to the expected spot
        candidates = [
            (hx, hy)
            for hx, hy, hd in vision.detect_head_pixels(frame, grid.cell_w)
            if hd == direction
        ]
        best: tuple[int, int] | None = None
        best_dist = float("inf")
        for hx, hy in candidates:
            dist = ((hx - sx) ** 2 + (hy - sy) ** 2) ** 0.5
            if dist < best_dist:
                best, best_dist = (hx, hy), dist
        if best is None or best_dist > grid.cell_w * 1.5:
            print(f"  target not found in view (nearest {best_dist:.0f}px) - stopping")
            return "stuck"
        sx, sy = best
        print(f"  move {move_number}: ({row},{col}) {direction} -> tap ({sx},{sy})")
        before = frame.copy()
        for hx, hy, _hd in vision.detect_head_pixels(frame, grid.cell_w):
            cv2.circle(before, (hx, hy), 6, (255, 0, 0), -1)
        cv2.circle(before, (sx, sy), 22, (0, 0, 255), 4)
        cv2.imwrite(os.path.join(args.outdir, f"cut_move_{move_number:03d}_before.png"), before)
        adb_client.tap(sx, sy)
        time.sleep(args.delay)
        for _ in range(6):
            frame = capture.get_frame()
            update_global(grid, occupancy, heads, frame, offset)
            if occupancy[row, col] == vision.EMPTY:
                failures = 0
                break
            time.sleep(0.25)
        else:
            failures += 1
            print(f"    arrow did not leave ({failures})")
            if failures >= 2:
                return "stuck"
        heads.pop((row, col), None)
        after = frame.copy()
        for hx, hy, _hd in vision.detect_head_pixels(frame, grid.cell_w):
            cv2.circle(after, (hx, hy), 6, (255, 0, 0), -1)
        cv2.imwrite(os.path.join(args.outdir, f"cut_move_{move_number:03d}_after.png"), after)
    return "max_moves"


def main() -> None:
    parser = argparse.ArgumentParser(description="Play Arrows automatically.")
    parser.add_argument("--simulate", action="store_true", help="Only list playable moves, do not tap")
    parser.add_argument("--all", action="store_true", help="Play level after level")
    parser.add_argument("--levels", type=int, default=None, help="Stop after this many levels (with --all)")
    parser.add_argument("--max-moves", type=int, default=300, help="Safety limit of taps per level")
    parser.add_argument("--delay", type=float, default=0.3, help="Extra delay after each tap (s)")
    parser.add_argument("--outdir", default="imgs/grid_finder", help="Directory for debug images")
    args = parser.parse_args()

    adb_client.ensure_device()

    if args.simulate:
        detected = detect_stable_grid()
        if detected is None:
            print("Could not detect the board. Is the game visible on screen?")
            return
        frame, grid = detected
        heads = vision.detect_arrowheads(frame, grid)
        occupancy = vision.build_occupancy(frame, grid)
        moves = solver.playable_moves(grid, occupancy, heads)
        print(f"Grid {grid.cols}x{grid.rows} | arrows {len(heads)} | playable {len(moves)}")
        for row, col, direction in moves:
            x, y = grid.cell_center(row, col)
            print(f"  ({row},{col}) {direction} -> tap ({x},{y})")
        save_debug(os.path.join(args.outdir, "simulate.png"), frame, grid, heads, occupancy)
        return

    if not args.all:
        frame = wait_for_stable()
        if board_is_cut(frame):
            print("Board is larger than the screen - stitching first...")
            print(f"Result: {play_cut_level(frame, args)}")
            return
        grid = detect_board(frame)
        if grid is None or not vision.detect_arrowheads(frame, grid):
            print("Could not detect the board. Is the game visible on screen?")
            return
        print(f"Grid: {grid.cols}x{grid.rows} cells, cell={grid.cell_w:.1f}x{grid.cell_h:.1f}px, pad={grid.pad}")
        print(f"Result: {play_level(frame, grid, args)}")
        return

    level = 0
    while args.levels is None or level < args.levels:
        detected = wait_for_board_with_arrows()
        if detected is None:
            continue
        frame, grid = detected
        level += 1
        if board_is_cut(frame):
            print(f"Level {level}: board larger than screen - stitching")
            result = play_cut_level(frame, args)
        else:
            print(f"Level {level}: grid {grid.cols}x{grid.rows}, cell={grid.cell_w:.1f}px")
            result = play_level(frame, grid, args)
        print(f"Level {level}: {result}")
        if result != "completed":
            print("Stopping.")
            break
        print("Level complete. Start the next level (handle any ads) - waiting...")
    print("Stopped.")


if __name__ == "__main__":
    main()
