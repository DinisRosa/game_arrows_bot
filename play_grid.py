"""Play a stitched (larger-than-screen) level from the grid-level model.

Workflow:
  1. Build the global model by chaining grid alignments over the captured frames.
  2. Locate the current view inside the model (align the live frame to it).
  3. For each solver move, pan until the target is visible, tap the head,
     verify the arrow left and update the model.
"""

from __future__ import annotations

import argparse
import os
import time

import cv2
import numpy as np

import adb_client
import capture
import grid_stitch as gs
import solver
import stitch
import vision


def locate_view(
    model_symbols: np.ndarray,
    frame: np.ndarray,
    cell: float,
    guess: tuple[int, int] | None = None,
    window: int = 8,
    min_support: int = 40,
    min_score: float = 0.7,
):
    """Align a live frame to the model.

    Returns (frame_grid, (vr, vc), alignment) where the frame's local cell (0, 0)
    corresponds to model cell (vr, vc).
    """
    frame_grid = gs.extract_grid(frame, cell, source="view")
    model_grid = gs.FrameGrid(symbols=model_symbols, cell=cell, phase=(0.0, 0.0))
    rows, cols = model_symbols.shape
    if guess is None:
        dr_range = range(-frame_grid.rows + 1, rows)
        dc_range = range(-frame_grid.cols + 1, cols)
    else:
        dr_range = range(guess[0] - window, guess[0] + window + 1)
        dc_range = range(guess[1] - window, guess[1] + window + 1)
    alignment = gs.align(
        model_grid,
        frame_grid,
        dc_range=dc_range,
        dr_range=dr_range,
        min_support=min_support,
        min_score=min_score,
    )
    if alignment is None:
        return None
    return frame_grid, (alignment.dr, alignment.dc), alignment


def pan_toward(
    adb_client,
    frame_grid: gs.FrameGrid,
    view: tuple[int, int],
    row: int,
    col: int,
    margin: int = 3,
    step: int = 350,
) -> str | None:
    """Pan one axis to bring model cell (row, col) into the blue (allowed) area."""
    cell = frame_grid.cell
    top = int(np.ceil(stitch.TOP_MARGIN / cell)) + 1
    bottom = int(np.ceil(stitch.BOTTOM_MARGIN / cell)) + 1
    vr, vc = view
    local_r, local_c = row - vr, col - vc
    over_r = 0
    if local_r < top:
        over_r = local_r - top
    elif local_r > frame_grid.rows - 1 - bottom:
        over_r = local_r - (frame_grid.rows - 1 - bottom)
    over_c = 0
    if local_c < margin:
        over_c = local_c - margin
    elif local_c > frame_grid.cols - 1 - margin:
        over_c = local_c - (frame_grid.cols - 1 - margin)
    if over_r == 0 and over_c == 0:
        return None
    if abs(over_c) >= abs(over_r) and over_c != 0:
        direction = "L" if over_c < 0 else "R"
    else:
        direction = "U" if over_r < 0 else "D"
    stitch._pan(adb_client, direction, step)
    time.sleep(0.5)
    return direction


def arrow_left(frame: np.ndarray, sx: int, sy: int, cell: float, ratio: float = 0.15) -> bool:
    """Whether the area around (sx, sy) is now free of arrow pixels."""
    mask = vision.foreground_mask(frame)
    half = int(cell * 0.4)
    patch = mask[max(0, sy - half) : sy + half, max(0, sx - half) : sx + half]
    if patch.size == 0:
        return False
    return float(patch.mean()) < ratio


def refresh(
    model_symbols: np.ndarray,
    occupancy: np.ndarray,
    heads: dict,
    frame_grid: gs.FrameGrid,
    view: tuple[int, int],
    margin: int = 2,
) -> None:
    """Make the live frame authoritative for the cells it shows.

    Arrows that left the board disappear from the live frame, so this keeps the
    model (used both for alignment and for the solver) in sync with the game.
    """
    vr, vc = view
    rows, cols = model_symbols.shape
    for r in range(margin, frame_grid.rows - margin):
        for c in range(margin, frame_grid.cols - margin):
            gr, gc = vr + r, vc + c
            if not (0 <= gr < rows and 0 <= gc < cols):
                continue
            symbol = int(frame_grid.symbols[r, c])
            if symbol == gs.UNKNOWN:
                continue
            model_symbols[gr, gc] = symbol
            if symbol == gs.EMPTY:
                occupancy[gr, gc] = vision.EMPTY
                heads.pop((gr, gc), None)
            else:
                occupancy[gr, gc] = vision.OCCUPIED
                if symbol in gs.HEAD_DIR:
                    heads[(gr, gc)] = gs.HEAD_DIR[symbol]


def relocate(
    model_symbols: np.ndarray,
    occupancy: np.ndarray,
    heads: dict,
    frame: np.ndarray,
    cell: float,
    guess: tuple[int, int],
    window: int,
    min_score: float = 0.6,
):
    """Locate the live frame and refresh the model from it.

    Tries a windowed search around `guess` first (fast); if that fails, falls back
    to a full search over the whole model so a big/unknown pan does not lose track.
    """
    located = locate_view(model_symbols, frame, cell, guess=guess, window=window, min_score=min_score)
    if located is None and guess is not None:
        located = locate_view(model_symbols, frame, cell, guess=None, min_score=0.4, min_support=20)
    if located is None:
        return None
    frame_grid, view, alignment = located
    refresh(model_symbols, occupancy, heads, frame_grid, view)
    return frame_grid, view, alignment


def build_model(frames_dir: str, cell: float = 0.0):
    """Load the frames and build (grid, occupancy, heads, symbols, cell, stats)."""
    frames = stitch.load_frames(frames_dir)
    symbols, provenance, positions, cell, stats, grids = gs.stitch_all(
        frames, cell=cell or None
    )
    grid, occupancy, heads, model_symbols, trim = gs.to_model(
        symbols, provenance, cell, grids, positions
    )
    return grid, occupancy, heads, model_symbols, cell, stats


def initial_locate(model_symbols, occupancy, heads, cell):
    """Locate the current view with a full search and refresh the model."""
    frame = capture.get_frame()
    located = locate_view(model_symbols, frame, cell, guess=None)
    if located is None:
        located = locate_view(model_symbols, frame, cell, guess=None, min_score=0.4, min_support=20)
    if located is None:
        return None
    frame_grid, view, alignment = located
    refresh(model_symbols, occupancy, heads, frame_grid, view)
    return frame, frame_grid, view, alignment


def play(args: argparse.Namespace) -> str:
    if args.recapture:
        print("re-capturing the board...")
        stitch.capture_frames(adb_client, outdir=args.frames_dir)
    grid, occupancy, heads, model_symbols, cell, stats = build_model(args.frames_dir, args.cell)
    print(
        f"model: {grid.cols}x{grid.rows} cells, cell={cell:.2f}px, heads={len(heads)}, "
        f"conflicts={stats['conflicts']} ({stats['conflict_ratio'] * 100:.1f}%)"
    )

    located = initial_locate(model_symbols, occupancy, heads, cell)
    if located is None:
        print("  could not locate the current view in the model")
        return "stuck"
    frame, frame_grid, view, alignment = located
    print(
        f"view at model cell {view} (score={alignment.score:.3f} support={alignment.support})"
    )

    os.makedirs(args.moves_dir, exist_ok=True)
    failures = 0
    refreshes = 0
    blocked: set[tuple[int, int]] = set()
    top_cells = int(np.ceil(stitch.TOP_MARGIN / cell)) + 1
    bottom_cells = int(np.ceil(stitch.BOTTOM_MARGIN / cell)) + 1
    for move_number in range(1, args.max_moves + 1):
        moves = [
            move
            for move in solver.playable_moves(grid, occupancy, heads)
            if (move[0], move[1]) not in blocked
        ]
        if not moves and heads and refreshes < args.max_refreshes:
            refreshes += 1
            print(f"  no moves ({len(heads)} heads remain) - resetting to top-left and re-stitching (refresh {refreshes})")
            stitch.pan_to_top_left(adb_client)
            stitch.clear_dir(args.frames_dir)
            stitch.clear_dir("imgs/grids")
            stitch.clear_dir("imgs/stitching")
            stitch.capture_frames(adb_client, outdir=args.frames_dir)
            grid, occupancy, heads, model_symbols, cell, stats = build_model(
                args.frames_dir, args.cell
            )
            top_cells = int(np.ceil(stitch.TOP_MARGIN / cell)) + 1
            bottom_cells = int(np.ceil(stitch.BOTTOM_MARGIN / cell)) + 1
            located = initial_locate(model_symbols, occupancy, heads, cell)
            if located is None:
                print("  could not locate the view after refresh")
                return "stuck"
            frame, frame_grid, view, alignment = located
            blocked.clear()
            moves = [
                move
                for move in solver.playable_moves(grid, occupancy, heads)
                if (move[0], move[1]) not in blocked
            ]
        if not moves:
            if not heads:
                return "completed"
            return "stuck"

        row, col, direction = moves[0]

        in_view = False
        for _ in range(args.max_pans):
            local_r, local_c = row - view[0], col - view[1]
            sx = int(round(frame_grid.x0 + local_c * cell))
            sy = int(round(frame_grid.y0 + local_r * cell))
            if (
                top_cells <= local_r <= frame_grid.rows - 1 - bottom_cells
                and 3 <= local_c <= frame_grid.cols - 1 - 3
                and vision.tap_allowed(args.tap_mask, sx, sy)
            ):
                in_view = True
                break
            panned = pan_toward(adb_client, frame_grid, view, row, col)
            if panned is None:
                break
            frame = capture.get_frame()
            relocated = relocate(model_symbols, occupancy, heads, frame, cell, view, 16)
            if relocated is None:
                print("  lost the view while panning")
                return "stuck"
            frame_grid, view, alignment = relocated

        local_r, local_c = row - view[0], col - view[1]
        if not in_view or not (top_cells <= local_r <= frame_grid.rows - 1 - bottom_cells and 3 <= local_c <= frame_grid.cols - 1 - 3):
            print(f"  target ({row},{col}) is not inside visible bounds - skipping")
            blocked.add((row, col))
            continue

        sx = int(round(frame_grid.x0 + local_c * cell))
        sy = int(round(frame_grid.y0 + local_r * cell))
        if not vision.tap_allowed(args.tap_mask, sx, sy):
            print("  target under the UI - skipping this move")
            blocked.add((row, col))
            continue

        candidates = [
            (hx, hy)
            for hx, hy, head_direction in vision.detect_head_pixels(frame, cell)
            if head_direction == direction
        ]
        best: tuple[int, int] | None = None
        best_dist = float("inf")
        for hx, hy in candidates:
            distance = ((hx - sx) ** 2 + (hy - sy) ** 2) ** 0.5
            if distance < best_dist:
                best, best_dist = (hx, hy), distance
        if best is None or best_dist > cell * 1.5:
            print(
                f"  target head not found (nearest {best_dist:.0f}px) - skipping"
            )
            blocked.add((row, col))
            continue
        sx, sy = best

        print(f"  move {move_number}: ({row},{col}) {direction} -> tap ({sx},{sy})")
        before = frame.copy()
        cv2.circle(before, (sx, sy), 22, (0, 0, 255), 4)
        cv2.imwrite(os.path.join(args.moves_dir, f"play_move_{move_number:03d}_before.png"), before)

        adb_client.tap(sx, sy)
        time.sleep(args.delay)

        for _ in range(6):
            frame = capture.get_frame()
            if arrow_left(frame, sx, sy, cell):
                failures = 0
                break
            time.sleep(0.25)
        else:
            failures += 1
            print(f"    arrow did not leave ({failures})")
            if failures >= 2:
                return "stuck"

        heads.pop((row, col), None)
        occupancy[row, col] = vision.EMPTY
        frame = capture.get_frame()
        relocated = relocate(model_symbols, occupancy, heads, frame, cell, view, 6)
        if relocated is not None:
            frame_grid, view, alignment = relocated
        cv2.imwrite(os.path.join(args.moves_dir, f"play_move_{move_number:03d}_after.png"), frame)
    return "max_moves"


def main() -> None:
    parser = argparse.ArgumentParser(description="Play a stitched level from the grid model")
    parser.add_argument("--frames-dir", default="imgs/frames")
    parser.add_argument("--cell", type=float, default=0.0, help="0 = auto-detect")
    parser.add_argument("--recapture", action="store_true", help="re-capture the board before playing")
    parser.add_argument("--max-refreshes", type=int, default=3, help="re-captures when no moves remain")
    parser.add_argument("--max-moves", type=int, default=300)
    parser.add_argument("--max-pans", type=int, default=10)
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--moves-dir", default="imgs/moves")
    parser.add_argument("--mask", default="imgs/mask/mask.png", help="Tap mask (blue = allowed)")
    args = parser.parse_args()

    try:
        args.tap_mask = vision.load_tap_mask(args.mask)
    except FileNotFoundError as error:
        print(f"ERROR: {error}. The bot refuses to tap without a valid mask.")
        return

    adb_client.ensure_device()
    print(f"Result: {play(args)}")


if __name__ == "__main__":
    main()
