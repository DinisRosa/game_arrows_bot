"""Reconstruct a board larger than the screen by panning and merging frames.

The board pans freely (content follows the finger) and is clamped at its edges.
Frames are captured in a boustrophedon raster (scan right, move down, scan left,
...) and each is aligned to the previous one by template matching, giving a 2D
cumulative pixel offset. Cells are mapped to global grid coordinates using the
lattice (cell size and phase) of a reference frame.

The top and bottom screen margins hold the game UI, so cells are only classified
inside the viewport band [TOP_MARGIN, height - BOTTOM_MARGIN].
"""

from __future__ import annotations

import os
import time

import cv2
import numpy as np

import adb_client
import capture
import vision

TOP_MARGIN = 400
BOTTOM_MARGIN = 300


def _shift(a: np.ndarray, b: np.ndarray, direction: str) -> tuple[float, float, float]:
    """Pixel shift of frame b relative to frame a (template match).

    The patch is taken from inside the board on the side that stays within the
    overlap for the given pan direction.
    """
    height, width = a.shape[:2]
    box = vision.detect_board_box(a)
    if box is not None:
        bx0, by0, bx1, by1 = box
        patch_w = max(60, (bx1 - bx0) // 3)
        patch_h = max(60, (by1 - by0) // 3)
        if direction == "R":
            x0, x1 = max(bx0, bx1 - patch_w - 20), max(bx0 + 60, bx1 - 20)
            y0, y1 = by0 + 20, by1 - 20
        elif direction == "L":
            x0, x1 = min(bx1 - 60, bx0 + 20), min(bx1, bx0 + 20 + patch_w)
            y0, y1 = by0 + 20, by1 - 20
        elif direction == "D":
            x0, x1 = bx0 + 20, bx1 - 20
            y0, y1 = max(by0, by1 - patch_h - 20), max(by0 + 60, by1 - 20)
        else:  # U
            x0, x1 = bx0 + 20, bx1 - 20
            y0, y1 = min(by1 - 60, by0 + 20), min(by1, by0 + 20 + patch_h)
    else:
        y0, y1 = TOP_MARGIN + 20, height - BOTTOM_MARGIN - 20
        x0, x1 = 60, width - 60
    patch = a[y0:y1, x0:x1]
    if patch.size == 0 or b.shape[0] < patch.shape[0] or b.shape[1] < patch.shape[1]:
        return 0.0, 0.0, 0.0
    result = cv2.matchTemplate(b, patch, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(result)
    return float(loc[0] - x0), float(loc[1] - y0), float(score)


def _pan(adb_client, direction: str, step: int, duration: int = 280) -> None:
    width, height = adb_client.screen_size()
    cx, cy = width // 2, height // 2
    half = step // 2
    if direction == "R":
        adb_client.swipe(cx + half, cy, cx - half, cy, duration)
    elif direction == "L":
        adb_client.swipe(cx - half, cy, cx + half, cy, duration)
    elif direction == "D":
        adb_client.swipe(cx, cy + half, cx, cy - half, duration)
    else:  # U
        adb_client.swipe(cx, cy - half, cx, cy + half, duration)


def _scan(
    adb_client,
    direction: str,
    step: int,
    max_steps: int,
    settle: float,
    previous: np.ndarray,
    offset: tuple[float, float],
    frames: list,
    outdir: str | None,
) -> tuple[np.ndarray, tuple[float, float]]:
    """Pan in one horizontal direction until clamped, recording frames."""
    ox, oy = offset
    for _ in range(max_steps):
        _pan(adb_client, direction, step)
        time.sleep(settle)
        current = capture.get_frame()
        dx, dy, score = _shift(previous, current, direction)
        print(f"  {direction} shift=({dx:.0f},{dy:.0f}) score={score:.2f}", flush=True)
        if score < 0.4 or abs(dy) > 90 or (abs(dx) < 5 and abs(dy) < 5):
            break
        ox -= dx
        oy -= dy
        frames.append((current, (ox, oy)))
        if outdir:
            cv2.imwrite(os.path.join(outdir, f"stitch_frame_{len(frames) - 1:02d}.png"), current)
        previous = current
    return previous, (ox, oy)


def capture_grid(
    adb_client,
    step: int = 400,
    max_steps: int = 8,
    settle: float = 0.5,
    outdir: str | None = None,
):
    """Return [(frame, (offset_x, offset_y))] covering the board (2D raster)."""
    if outdir:
        os.makedirs(outdir, exist_ok=True)

    frame = capture.get_frame()
    for direction in ("U", "L"):  # go to the top-left corner
        previous = frame
        for _ in range(max_steps):
            _pan(adb_client, direction, step)
            time.sleep(settle)
            current = capture.get_frame()
            dx, dy, score = _shift(previous, current, direction)
            print(f"  {direction} shift=({dx:.0f},{dy:.0f}) score={score:.2f}", flush=True)
            if score < 0.4 or (abs(dx) < 5 and abs(dy) < 5):
                break
            previous = current
        frame = previous

    frames = [(frame, (0.0, 0.0))]
    if outdir:
        cv2.imwrite(os.path.join(outdir, "stitch_frame_00.png"), frame)

    ox = oy = 0.0
    direction = "R"
    for _ in range(max_steps):
        frame, (ox, oy) = _scan(
            adb_client, direction, step, max_steps, settle, frame, (ox, oy), frames, outdir
        )
        # move down one band
        previous = frame
        _pan(adb_client, "D", step)
        time.sleep(settle)
        current = capture.get_frame()
        dx, dy, score = _shift(previous, current, "D")
        print(f"  D shift=({dx:.0f},{dy:.0f}) score={score:.2f}", flush=True)
        if score < 0.4 or abs(dy) < 5:
            break  # bottom edge reached
        ox -= dx
        oy -= dy
        frames.append((current, (ox, oy)))
        if outdir:
            cv2.imwrite(os.path.join(outdir, f"stitch_frame_{len(frames) - 1:02d}.png"), current)
        frame = current
        direction = "L" if direction == "R" else "R"
    return frames


def save_frame_grids(frames, outdir: str = "imgs/stitching") -> None:
    """Save each captured frame and its arrow-based grid/heads for inspection."""
    os.makedirs(outdir, exist_ok=True)
    for index, (frame, offset) in enumerate(frames):
        cv2.imwrite(os.path.join(outdir, f"frame_{index:02d}.png"), frame)
        try:
            grid, method = vision.detect_grid(frame)
            heads = vision.detect_arrowheads(frame, grid)
            vis = vision.draw_heads(vision.draw_grid(frame, grid), grid, heads)
            cv2.imwrite(os.path.join(outdir, f"grid_{index:02d}.png"), vis)
            print(
                f"  frame {index}: [{method}] cell={grid.cell_w:.1f} grid={grid.cols}x{grid.rows} "
                f"heads={len(heads)} offset=({offset[0]:.0f},{offset[1]:.0f})",
                flush=True,
            )
        except Exception as error:  # noqa: BLE001
            print(f"  frame {index}: grid failed ({error})", flush=True)


def _frame_phase(frame: np.ndarray, cell: float) -> tuple[float, float]:
    """Phase (a cell centre, mod cell) of a frame, using the given cell size."""
    dots = vision.detect_dots(frame)
    box = vision.board_box_foreground(frame)
    if box is not None and len(dots) >= 5:
        inside = [d for d in dots if box[0] <= d[0] <= box[2] and box[1] <= d[1] <= box[3]]
        if len(inside) >= 5:
            x0, _cw = vision._refine_lattice([d[0] for d in inside], cell)
            y0, _ch = vision._refine_lattice([d[1] for d in inside], cell)
            return x0 % cell, y0 % cell
    return vision.estimate_phase(frame, cell)


def _snap(value: float, target: float, cell: float) -> float:
    """Shift `value` by less than half a cell so that value % cell == target % cell."""
    return value + (((target - value + cell / 2) % cell) - cell / 2)


def snap_offsets(frames, base_grid: vision.Grid):
    """Snap each frame offset to the lattice using its own detected phase.

    The board lattice is fixed in global coordinates, so a frame's detected phase
    tells us the sub-cell part of its offset. Correcting only that sub-cell part
    removes the cumulative drift without moving the offset by whole cells.
    """
    cell = base_grid.cell_w
    phase_ref_x = base_grid.x0 % cell
    phase_ref_y = base_grid.y0 % cell
    snapped = []
    for index, (frame, (ox, oy)) in enumerate(frames):
        raw = (ox, oy)
        try:
            fx, fy = _frame_phase(frame, cell)
            ox = _snap(ox, phase_ref_x - fx, cell)
            oy = _snap(oy, phase_ref_y - fy, cell)
            print(
                f"  snap {index}: raw=({raw[0]:.0f},{raw[1]:.0f}) -> ({ox:.0f},{oy:.0f})",
                flush=True,
            )
        except Exception:  # noqa: BLE001
            pass
        snapped.append((frame, (ox, oy)))
    return snapped


def merge(frames, base_grid: vision.Grid):
    """Merge frames into a global (grid, occupancy, heads)."""
    cell_w, cell_h = base_grid.cell_w, base_grid.cell_h
    phase_x, phase_y = base_grid.x0, base_grid.y0
    frame_h, frame_w = frames[0][0].shape[:2]
    top = TOP_MARGIN
    bottom = frame_h - BOTTOM_MARGIN

    occupancy: dict[tuple[int, int], int] = {}
    heads: dict[tuple[int, int], str] = {}

    for frame, (ox, oy) in frames:
        mask = vision.foreground_mask(frame)
        first_col = int(np.ceil((0 - phase_x + ox) / cell_w))
        last_col = int(np.floor((frame_w - phase_x + ox) / cell_w))
        first_row = int(np.ceil((0 - phase_y + oy) / cell_h))
        last_row = int(np.floor((frame_h - phase_y + oy) / cell_h))
        for row in range(first_row, last_row + 1):
            sy = phase_y - oy + row * cell_h
            y0 = int(round(sy - cell_h / 2))
            y1 = int(round(sy + cell_h / 2))
            if y0 < top or y1 > bottom:
                continue  # inside the UI margins
            for col in range(first_col, last_col + 1):
                sx = phase_x - ox + col * cell_w
                x0 = int(round(sx - cell_w / 2))
                x1 = int(round(sx + cell_w / 2))
                if x0 < 0 or x1 > frame_w:
                    continue
                ratio = float(mask[y0:y1, x0:x1].mean())
                value = vision.OCCUPIED if ratio >= 0.08 else vision.EMPTY
                key = (row, col)
                if value == vision.OCCUPIED or key not in occupancy:
                    occupancy[key] = value
        for x, y, direction in vision.detect_head_pixels(frame, cell_w):
            if not (top <= y <= bottom):
                continue
            row = int(round((y + oy - phase_y) / cell_h))
            col = int(round((x + ox - phase_x) / cell_w))
            heads[(row, col)] = direction

    rows = [r for r, _ in occupancy]
    cols = [c for _, c in occupancy]
    if not rows or not cols:
        raise ValueError("No cells captured")
    min_row, max_row = min(rows), max(rows)
    min_col, max_col = min(cols), max(cols)
    rows_count = max_row - min_row + 1
    cols_count = max_col - min_col + 1
    global_grid = vision.Grid(
        x0=phase_x + min_col * cell_w,
        y0=phase_y + min_row * cell_h,
        cell_w=cell_w,
        cell_h=cell_h,
        cols=cols_count,
        rows=rows_count,
        pad=0,
    )
    occupancy_matrix = np.full((rows_count, cols_count), vision.UNKNOWN, dtype=np.uint8)
    for (row, col), value in occupancy.items():
        occupancy_matrix[row - min_row, col - min_col] = value
    global_heads = {(row - min_row, col - min_col): d for (row, col), d in heads.items()}
    return global_grid, occupancy_matrix, global_heads


def render_board(grid: vision.Grid, occupancy: np.ndarray, heads: dict) -> np.ndarray:
    """Render a schematic image of the merged global board."""
    scale = 40
    margin = 10
    image = np.full(
        (grid.rows * scale + 2 * margin, grid.cols * scale + 2 * margin, 3), 255, np.uint8
    )
    vectors = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}
    for row in range(grid.rows):
        for col in range(grid.cols):
            x0 = col * scale + margin
            y0 = row * scale + margin
            value = occupancy[row, col]
            fill = (255, 255, 255)
            if value == vision.OCCUPIED:
                fill = (200, 200, 200)
            elif value == vision.UNKNOWN:
                fill = (255, 0, 255)
            cv2.rectangle(image, (x0, y0), (x0 + scale, y0 + scale), fill, -1)
            cv2.rectangle(image, (x0, y0), (x0 + scale, y0 + scale), (220, 220, 220), 1)
    for (row, col), direction in heads.items():
        x = col * scale + margin + scale // 2
        y = row * scale + margin + scale // 2
        dx, dy = vectors[direction]
        cv2.arrowedLine(image, (x, y), (x + dx * scale // 3, y + dy * scale // 3), (0, 0, 255), 2)
    return image


def stitch(adb_client, step: int = 400, max_steps: int = 8, outdir: str | None = "imgs/grid_finder"):
    """Capture and merge the full board (2D)."""
    frames = capture_grid(adb_client, step=step, max_steps=max_steps, outdir=outdir)
    save_frame_grids(frames)

    def dot_count(item) -> int:
        return len(vision.detect_dots(item[0]))

    ref_index = max(range(len(frames)), key=lambda i: dot_count(frames[i]))
    ref_frame, (ref_x, ref_y) = frames[ref_index]
    frames = [(frame, (ox - ref_x, oy - ref_y)) for frame, (ox, oy) in frames]

    base_grid, method = vision.detect_grid(ref_frame)
    print(f"  calibration: [{method}] cell={base_grid.cell_w:.1f}", flush=True)
    frames = snap_offsets(frames, base_grid)
    grid, occupancy, heads = merge(frames, base_grid)
    current_offset = frames[-1][1]
    if outdir:
        os.makedirs(outdir, exist_ok=True)
        cv2.imwrite(os.path.join(outdir, "stitch_merged.png"), render_board(grid, occupancy, heads))
    return grid, occupancy, heads, current_offset


def capture_frames(
    adb_client, step: int = 400, max_steps: int = 8, outdir: str = "imgs/frames"
):
    """Capture the board frames and save them (no merge) for grid-level stitching."""
    frames = capture_grid(adb_client, step=step, max_steps=max_steps)
    os.makedirs(outdir, exist_ok=True)
    lines = []
    for index, (frame, (ox, oy)) in enumerate(frames):
        cv2.imwrite(os.path.join(outdir, f"frame_{index:02d}.png"), frame)
        lines.append(f"{index} {ox:.0f} {oy:.0f}")
        print(f"  frame {index:02d}: offset=({ox:.0f},{oy:.0f})", flush=True)
    with open(os.path.join(outdir, "offsets.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return frames


def capture_n(
    adb_client,
    count: int = 2,
    step: int = 400,
    settle: float = 0.5,
    direction: str = "R",
    outdir: str = "imgs/frames",
):
    """Capture `count` frames while panning in one direction (simple sequence).

    Frame 0 is the current view; each next frame is captured after a pan, so
    consecutive frames overlap. Saves the raw frames and the offsets manifest.
    """
    os.makedirs(outdir, exist_ok=True)
    frame = capture.get_frame()
    ox = oy = 0.0
    frames = [(frame, (ox, oy))]
    previous = frame
    for _ in range(count - 1):
        _pan(adb_client, direction, step)
        time.sleep(settle)
        current = capture.get_frame()
        dx, dy, score = _shift(previous, current, direction)
        print(f"  {direction} shift=({dx:.0f},{dy:.0f}) score={score:.2f}", flush=True)
        if score < 0.4 or (abs(dx) < 5 and abs(dy) < 5):
            print("  pan stopped early (edge reached)")
            break
        ox -= dx
        oy -= dy
        frames.append((current, (ox, oy)))
        previous = current
    lines = []
    for index, (image, (fx, fy)) in enumerate(frames):
        cv2.imwrite(os.path.join(outdir, f"frame_{index:02d}.png"), image)
        lines.append(f"{index} {fx:.0f} {fy:.0f}")
        print(f"  frame {index:02d}: offset=({fx:.0f},{fy:.0f})", flush=True)
    with open(os.path.join(outdir, "offsets.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return frames


def load_frames(outdir: str = "imgs/frames"):
    """Load saved frames and their offsets manifest as [(image, (ox, oy))]."""
    frames = []
    with open(os.path.join(outdir, "offsets.txt"), encoding="utf-8") as handle:
        for line in handle:
            parts = line.split()
            if len(parts) != 3:
                continue
            index, ox, oy = int(parts[0]), float(parts[1]), float(parts[2])
            image = cv2.imread(os.path.join(outdir, f"frame_{index:02d}.png"))
            if image is None:
                raise FileNotFoundError(f"missing frame_{index:02d}.png")
            frames.append((image, (ox, oy)))
    return frames



if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Capture/merge the board")
    parser.add_argument("--capture-only", action="store_true", help="only capture frames")
    parser.add_argument("--step", type=int, default=400)
    parser.add_argument("--max-steps", type=int, default=8)
    args = parser.parse_args()

    adb_client.ensure_device()
    if args.capture_only:
        frames = capture_frames(adb_client, step=args.step, max_steps=args.max_steps)
        print(f"Captured {len(frames)} frames in imgs/frames/")
    else:
        grid, occupancy, heads, offset = stitch(adb_client)
        print(f"Global board: {grid.cols}x{grid.rows} cells, cell={grid.cell_w:.1f}px, heads={len(heads)}")
        print(f"Current view offset: ({offset[0]:.0f}, {offset[1]:.0f})")
        print("Saved frames and stitch_merged.png in imgs/grid_finder/")
