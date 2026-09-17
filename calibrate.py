"""Interactive grid calibration.

Captures the device screen, detects the light-gray grid dots and builds the cell
grid. The board limits are auto-detected, but you can override them by clicking
two corners (top-left and bottom-right). The result is shown overlaid and saved
as JSON.

Usage:
    ./venv/bin/python calibrate.py [--image PATH] [--out configs/calibration.json]

Keys:
    left click   set board corners (first top-left, then bottom-right)
    r            reset to the auto-detected board
    y            save the calibration
    q / ESC      quit
"""

from __future__ import annotations

import argparse
import json
import os

import cv2

import adb_client
import vision

WINDOW = "Auto-ARROWS - calibrate"
MAX_WINDOW_HEIGHT = 900


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate the board grid.")
    parser.add_argument("--image", help="Use an image file instead of a live screenshot")
    parser.add_argument("--out", default="configs/calibration.json", help="Output JSON path")
    args = parser.parse_args()

    if args.image:
        frame = cv2.imread(args.image, cv2.IMREAD_COLOR)
        if frame is None:
            raise SystemExit(f"Could not read image: {args.image}")
    else:
        adb_client.ensure_device()
        frame = adb_client.screenshot()

    height, width = frame.shape[:2]
    scale = min(1.0, MAX_WINDOW_HEIGHT / height)
    print(f"Image: {width}x{height} px")
    print("Keys: [click 2 corners to override] [r] reset  [y] save  [q]/[ESC] quit")

    dots = vision.detect_dots(frame)
    print(f"Detected dots: {len(dots)}")
    if len(dots) >= 2:
        print(f"Coarse cell size: {vision.nearest_neighbor_spacing(dots):.2f} px")

    clicks: list[tuple[int, int]] = []
    grid: vision.Grid | None = None
    display = frame.copy()

    def rebuild() -> None:
        nonlocal grid, display
        if len(clicks) == 2:
            (x1, y1), (x2, y2) = clicks
            board = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            source = "clicks"
        else:
            auto = vision.detect_board_box(frame)
            if auto is None:
                print("Could not detect the board")
                return
            board = auto
            source = "auto"
        inside = [
            d
            for d in dots
            if board[0] <= d[0] <= board[2] and board[1] <= d[1] <= board[3]
        ]
        try:
            grid = vision.build_grid(inside, board)
        except ValueError as error:
            grid = None
            print(f"Calibration failed: {error}")
            return
        print(
            f"[{source}] board={board} dots={len(inside)} -> "
            f"cell={grid.cell_w:.2f}x{grid.cell_h:.2f} px, "
            f"{grid.cols} cols x {grid.rows} rows, origin=({grid.x0:.1f}, {grid.y0:.1f})"
        )
        heads = vision.detect_arrowheads(frame, grid)
        print(f"Arrowheads detected: {len(heads)}")
        display = vision.draw_heads(vision.draw_grid(frame, grid), grid, heads)
        for dot in inside:
            cv2.circle(display, (int(dot[0]), int(dot[1])), 4, (0, 255, 0), 2)

    def on_mouse(event: int, x: int, y: int, flags: int, param: object) -> None:
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        device_x = int(round(x / scale))
        device_y = int(round(y / scale))
        if len(clicks) == 2:
            clicks.clear()
        clicks.append((device_x, device_y))
        print(f"corner {len(clicks)} -> ({device_x}, {device_y})")
        rebuild()

    rebuild()
    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WINDOW, on_mouse)

    while True:
        cv2.imshow(WINDOW, cv2.resize(display, None, fx=scale, fy=scale))
        key = cv2.waitKey(30) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord("r"):
            clicks.clear()
            rebuild()
        if key == ord("y"):
            if grid is None:
                print("nothing to save yet")
                continue
            os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
            with open(args.out, "w") as file:
                json.dump({"grid": grid.to_dict()}, file, indent=2)
            print(f"saved {args.out}")

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
