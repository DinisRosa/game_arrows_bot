"""Grid-level stitching: align and merge per-frame symbol grids.

Instead of overlapping raw pixels, each frame is reduced to a discrete grid of
symbols (empty, body, arrow head with direction). Two frames are aligned by the
integer cell translation that best matches their overlapping symbols, then merged
cell by cell, keeping provenance (A only / B only / both).

This is a proof of concept that works on two already-captured frames; the same
functions can later be chained over the whole raster.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field

import cv2
import numpy as np

import vision

TOP_MARGIN = 400
BOTTOM_MARGIN = 300

UNKNOWN, EMPTY, OCCUPIED = 0, 1, 2
HEAD_U, HEAD_D, HEAD_L, HEAD_R = 3, 4, 5, 6

CHAR = {
    UNKNOWN: "?",
    EMPTY: ".",
    OCCUPIED: "#",
    HEAD_U: "^",
    HEAD_D: "v",
    HEAD_L: "<",
    HEAD_R: ">",
}
CODE = {value: key for key, value in CHAR.items()}
HEAD_CODE = {"U": HEAD_U, "D": HEAD_D, "L": HEAD_L, "R": HEAD_R}
HEAD_DIR = {HEAD_U: "U", HEAD_D: "D", HEAD_L: "L", HEAD_R: "R"}
HEAD_VECTOR = {
    HEAD_U: (0, -1),
    HEAD_D: (0, 1),
    HEAD_L: (-1, 0),
    HEAD_R: (1, 0),
}
_RANK = {EMPTY: 0, OCCUPIED: 1, HEAD_U: 2, HEAD_D: 2, HEAD_L: 2, HEAD_R: 2}


@dataclass
class FrameGrid:
    """A frame reduced to a discrete symbol grid."""

    symbols: np.ndarray
    cell: float
    phase: tuple[float, float]
    source: str = ""
    offset: tuple[float, float] = (0.0, 0.0)
    x0: float = 0.0
    y0: float = 0.0

    @property
    def rows(self) -> int:
        return int(self.symbols.shape[0])

    @property
    def cols(self) -> int:
        return int(self.symbols.shape[1])


@dataclass
class Alignment:
    """Result of aligning grid B against grid A (B origin in A cell coords)."""

    dr: int
    dc: int
    score: float
    support: int
    raw: float
    alternatives: list[tuple[int, int, float, int]] = field(default_factory=list)

    @property
    def offset(self) -> tuple[int, int]:
        return self.dr, self.dc


def _lattice(
    width: int, height: int, cell: float, phase: tuple[float, float]
) -> tuple[float, float, int, int]:
    """Lattice origin/cols/rows so that every cell lies fully inside the frame."""
    px, py = phase
    half = cell / 2
    x0 = px + np.ceil((half - px) / cell) * cell
    y0 = py + np.ceil((half - py) / cell) * cell
    cols = int(np.floor((width - half - x0) / cell)) + 1
    rows = int(np.floor((height - half - y0) / cell)) + 1
    return float(x0), float(y0), max(cols, 0), max(rows, 0)


def frame_phase(frame: np.ndarray, cell: float) -> tuple[float, float]:
    """Lattice phase (a cell centre, mod cell) of a frame for the given cell."""
    dots = vision.detect_dots(frame)
    box = vision.board_box_foreground(frame)
    if box is not None and len(dots) >= 5:
        inside = [d for d in dots if box[0] <= d[0] <= box[2] and box[1] <= d[1] <= box[3]]
        if len(inside) >= 5:
            x0, _cw = vision._refine_lattice([d[0] for d in inside], cell)
            y0, _ch = vision._refine_lattice([d[1] for d in inside], cell)
            return x0 % cell, y0 % cell
    return vision.estimate_phase(frame, cell)


def extract_grid(
    frame: np.ndarray,
    cell: float,
    source: str = "",
    offset: tuple[float, float] = (0.0, 0.0),
    margin: bool = True,
) -> FrameGrid:
    """Reduce a frame to a discrete symbol grid on the given lattice."""
    height, width = frame.shape[:2]
    phase = frame_phase(frame, cell)
    x0, y0, cols, rows = _lattice(width, height, cell, phase)
    grid = vision.Grid(x0=x0, y0=y0, cell_w=cell, cell_h=cell, cols=cols, rows=rows, pad=0)
    occupancy = vision.build_occupancy(frame, grid)
    heads = vision.detect_arrowheads(frame, grid)
    symbols = np.full((rows, cols), UNKNOWN, dtype=np.uint8)
    symbols[occupancy == vision.OCCUPIED] = OCCUPIED
    symbols[occupancy == vision.EMPTY] = EMPTY
    for (row, col), direction in heads.items():
        if 0 <= row < rows and 0 <= col < cols:
            symbols[row, col] = HEAD_CODE[direction]
    if margin:
        top, bottom = TOP_MARGIN, height - BOTTOM_MARGIN
        for row in range(rows):
            center = y0 + row * cell
            if center < top or center > bottom:
                symbols[row, :] = UNKNOWN
    return FrameGrid(symbols=symbols, cell=cell, phase=phase, source=source, offset=offset, x0=x0, y0=y0)


def _pair_score(a: np.ndarray, b: np.ndarray) -> tuple[float, int, float]:
    """Score two aligned symbol patches.

    Returns (score, support, raw). `support` counts cells where at least one side
    is informative (not empty), `raw` is the weighted agreement and `score` is
    `raw / support` (1.0 = every informative cell agrees).
    """
    known = (a != UNKNOWN) & (b != UNKNOWN)
    a_occ = (a != UNKNOWN) & (a != EMPTY)
    b_occ = (b != UNKNOWN) & (b != EMPTY)
    informative = known & (a_occ | b_occ)
    support = int(informative.sum())
    if support == 0:
        return 0.0, 0, 0.0
    agree = informative & (a == b)
    both_occ = informative & a_occ & b_occ
    raw = float(agree.sum()) + 0.5 * float((both_occ & ~agree).sum())
    return raw / support, support, raw


def align(
    a: FrameGrid,
    b: FrameGrid,
    dc_range: range,
    dr_range: range,
    min_support: int = 2,
    min_score: float = 0.5,
    score_band: float = 0.05,
) -> Alignment | None:
    """Find (dr, dc) so that b[r, c] matches a[r + dr, c + dc] (integer cells).

    The winner is the translation with the highest agreement *score* (fraction of
    informative cells that agree), not the highest raw count: a wrong shift over a
    large overlap can match many cells by chance but never scores near 1.0. Ties
    within `score_band` are broken by support (more informative cells = safer).
    """
    rows_a, cols_a = a.symbols.shape
    rows_b, cols_b = b.symbols.shape
    candidates: list[tuple[int, int, float, int, float]] = []
    for dr in dr_range:
        r0 = max(0, -dr)
        r1 = min(rows_b, rows_a - dr)
        if r1 <= r0:
            continue
        for dc in dc_range:
            c0 = max(0, -dc)
            c1 = min(cols_b, cols_a - dc)
            if c1 <= c0:
                continue
            patch_a = a.symbols[r0 + dr : r1 + dr, c0 + dc : c1 + dc]
            patch_b = b.symbols[r0:r1, c0:c1]
            score, support, raw = _pair_score(patch_a, patch_b)
            if support < min_support:
                continue
            candidates.append((dr, dc, score, support, raw))
    if not candidates:
        return None
    best_score = max(candidate[2] for candidate in candidates)
    if best_score < min_score:
        return None
    band = [candidate for candidate in candidates if candidate[2] >= best_score - score_band]
    band.sort(key=lambda candidate: (-candidate[3], -candidate[4]))
    dr, dc, score, support, raw = band[0]
    best = Alignment(dr=dr, dc=dc, score=score, support=support, raw=raw)
    candidates.sort(key=lambda candidate: (-candidate[2], -candidate[3]))
    best.alternatives = [(c[0], c[1], c[2], c[3]) for c in candidates[:5]]
    return best


def _combine(symbol_a: int, symbol_b: int) -> tuple[int, bool]:
    """Combine two observations of a cell; returns (symbol, conflict)."""
    if symbol_a == UNKNOWN:
        return symbol_b, False
    if symbol_b == UNKNOWN:
        return symbol_a, False
    if symbol_a == symbol_b:
        return symbol_a, False
    return (symbol_a if _RANK[symbol_a] >= _RANK[symbol_b] else symbol_b), True


def merge(a: FrameGrid, b: FrameGrid, offset: tuple[int, int]):
    """Merge two grids; offset = (dr, dc): b's origin in a's cell coordinates."""
    dr, dc = offset
    rows_a, cols_a = a.symbols.shape
    rows_b, cols_b = b.symbols.shape
    min_r = min(0, dr)
    max_r = max(rows_a, dr + rows_b)
    min_c = min(0, dc)
    max_c = max(cols_a, dc + cols_b)
    rows, cols = max_r - min_r, max_c - min_c
    merged = np.full((rows, cols), UNKNOWN, dtype=np.uint8)
    provenance = np.zeros((rows, cols), dtype=np.uint8)  # 1=A, 2=B, 3=both
    conflicts = 0

    ar, ac = -min_r, -min_c
    br, bc = dr - min_r, dc - min_c
    for r in range(rows_a):
        for c in range(cols_a):
            value = a.symbols[r, c]
            if value != UNKNOWN:
                merged[ar + r, ac + c] = value
                provenance[ar + r, ac + c] |= 1
    for r in range(rows_b):
        for c in range(cols_b):
            value = b.symbols[r, c]
            if value == UNKNOWN:
                continue
            gr, gc = br + r, bc + c
            if 0 <= gr < rows and 0 <= gc < cols:
                merged[gr, gc], conflict = _combine(int(merged[gr, gc]), int(value))
                provenance[gr, gc] |= 2
                conflicts += int(conflict)

    stats = {
        "both": int((provenance == 3).sum()),
        "only_a": int((provenance == 1).sum()),
        "only_b": int((provenance == 2).sum()),
        "conflicts": conflicts,
        "heads": int(np.isin(merged, [HEAD_U, HEAD_D, HEAD_L, HEAD_R]).sum()),
    }
    return merged, provenance, stats


def merge_many(grids: list[FrameGrid], positions: list[tuple[int, int]]):
    """Merge many grids placed at integer-cell `positions` (row, col) with voting."""
    if len(grids) != len(positions):
        raise ValueError("grids and positions must have the same length")
    min_r = min(pr for pr, _pc in positions)
    max_r = max(pr + grid.rows for grid, (pr, _pc) in zip(grids, positions))
    min_c = min(pc for _pr, pc in positions)
    max_c = max(pc + grid.cols for grid, (_pr, pc) in zip(grids, positions))
    rows, cols = max_r - min_r, max_c - min_c
    votes: dict[tuple[int, int], dict[int, int]] = {}
    for grid, (pr, pc) in zip(grids, positions):
        base_r, base_c = pr - min_r, pc - min_c
        for r in range(grid.rows):
            for c in range(grid.cols):
                symbol = int(grid.symbols[r, c])
                if symbol == UNKNOWN:
                    continue
                cell = votes.setdefault((base_r + r, base_c + c), {})
                cell[symbol] = cell.get(symbol, 0) + 1
    symbols = np.full((rows, cols), UNKNOWN, dtype=np.uint8)
    provenance = np.zeros((rows, cols), dtype=np.uint8)
    conflicts = 0
    for (r, c), counts in votes.items():
        symbols[r, c] = max(counts, key=lambda s: (counts[s], _RANK[s]))
        provenance[r, c] = min(255, sum(counts.values()))
        if len(counts) > 1:
            conflicts += 1
    stats = {
        "cells": len(votes),
        "conflicts": conflicts,
        "conflict_ratio": conflicts / max(1, len(votes)),
        "heads": int(np.isin(symbols, [HEAD_U, HEAD_D, HEAD_L, HEAD_R]).sum()),
        "origin": (min_r, min_c),
    }
    return symbols, provenance, stats


def refine_positions(
    grids: list[FrameGrid],
    positions: list[tuple[int, int]],
    iterations: int = 3,
    window: int = 3,
    min_support: int = 20,
    min_score: float = 0.6,
) -> list[tuple[int, int]]:
    """Re-align every frame against the merged model to remove chained drift."""
    positions = list(positions)
    for _ in range(iterations):
        min_r = min(pr for pr, _pc in positions)
        min_c = min(pc for _pr, pc in positions)
        symbols, _prov, _stats = merge_many(grids, positions)
        model = FrameGrid(symbols=symbols, cell=grids[0].cell, phase=(0.0, 0.0))
        moved = 0
        for index, grid in enumerate(grids):
            pr, pc = positions[index]
            gr, gc = pr - min_r, pc - min_c
            alignment = align(
                model,
                grid,
                dc_range=range(gc - window, gc + window + 1),
                dr_range=range(gr - window, gr + window + 1),
                min_support=min_support,
                min_score=min_score,
            )
            if alignment is not None and (alignment.dr, alignment.dc) != (gr, gc):
                positions[index] = (alignment.dr + min_r, alignment.dc + min_c)
                moved += 1
        print(f"  refine: {moved} frames moved", flush=True)
        if moved == 0:
            break
    return positions


def stitch_all(
    frames: list[tuple[np.ndarray, tuple[float, float]]],
    cell: float | None = None,
    window: int = 4,
    wide: int = 10,
    min_support: int = 15,
    min_score: float = 0.6,
    refine: bool = True,
):
    """Chain-align every frame and merge them into one global symbol grid.

    `frames` is [(image, (offset_x, offset_y))] in capture order. The recorded
    pixel offsets are only used as a coarse guess for the integer-cell search;
    the grid content decides the final alignment.
    """
    images = [frame for frame, _offset in frames]
    offsets = [offset for _frame, offset in frames]
    if cell is None:
        cell = reference_cell(images)
    grids = [
        extract_grid(image, cell, source=f"frame_{index:02d}", offset=offset)
        for index, (image, offset) in enumerate(frames)
    ]
    positions: list[tuple[int, int]] = [(0, 0)]
    for index in range(1, len(grids)):
        prev, cur = grids[index - 1], grids[index]
        guess_dc = int(round((offsets[index][0] - offsets[index - 1][0]) / cell))
        guess_dr = int(round((offsets[index][1] - offsets[index - 1][1]) / cell))
        alignment = align(
            prev,
            cur,
            dc_range=range(guess_dc - window, guess_dc + window + 1),
            dr_range=range(guess_dr - window, guess_dr + window + 1),
            min_support=min_support,
            min_score=min_score,
        )
        source = "guess"
        if alignment is None:
            alignment = align(
                prev,
                cur,
                dc_range=range(-wide, wide + 1),
                dr_range=range(-wide, wide + 1),
                min_support=min_support,
                min_score=min_score,
            )
            source = "wide"
        if alignment is None:
            print(f"  pair {index - 1:02d}-{index:02d}: FAILED (using guess)", flush=True)
            pr, pc = positions[-1]
            positions.append((pr + guess_dr, pc + guess_dc))
            continue
        pr, pc = positions[-1]
        positions.append((pr + alignment.dr, pc + alignment.dc))
        print(
            f"  pair {index - 1:02d}-{index:02d}: guess=({guess_dr},{guess_dc}) [{source}] "
            f"-> (dr={alignment.dr}, dc={alignment.dc}) "
            f"score={alignment.score:.3f} support={alignment.support}",
            flush=True,
        )
    if refine:
        positions = refine_positions(grids, positions)
    symbols, provenance, stats = merge_many(grids, positions)
    stats["cell"] = cell
    return symbols, provenance, positions, cell, stats, grids


def to_model(symbols: np.ndarray, provenance: np.ndarray, cell: float, grids, positions):
    """Build (Grid, occupancy, heads, symbols, trim) for the solver from the merged symbols."""
    known = symbols != UNKNOWN
    known_rows = np.where(known.any(axis=1))[0]
    known_cols = np.where(known.any(axis=0))[0]
    trim_r0 = int(known_rows[0]) if len(known_rows) else 0
    trim_c0 = int(known_cols[0]) if len(known_cols) else 0
    symbols, provenance = trim_unknown(symbols, provenance)
    origin_r = min(pr for pr, _pc in positions)
    origin_c = min(pc for _pr, pc in positions)
    rows, cols = symbols.shape
    heads: dict[tuple[int, int], str] = {}
    occupancy = np.full((rows, cols), vision.EMPTY, dtype=np.uint8)
    for r in range(rows):
        for c in range(cols):
            symbol = int(symbols[r, c])
            if symbol == UNKNOWN:
                occupancy[r, c] = vision.UNKNOWN
            elif symbol == EMPTY:
                occupancy[r, c] = vision.EMPTY
            else:
                occupancy[r, c] = vision.OCCUPIED
            if symbol in HEAD_DIR:
                heads[(r, c)] = HEAD_DIR[symbol]
    x0 = grids[0].x0 + (origin_c + trim_c0) * cell
    y0 = grids[0].y0 + (origin_r + trim_r0) * cell
    grid = vision.Grid(x0=x0, y0=y0, cell_w=cell, cell_h=cell, cols=cols, rows=rows, pad=0)
    return grid, occupancy, heads, symbols, (trim_r0, trim_c0)


def trim_unknown(
    symbols: np.ndarray, provenance: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Drop border rows/columns that are entirely UNKNOWN (UI margins)."""
    known = symbols != UNKNOWN
    rows = np.where(known.any(axis=1))[0]
    cols = np.where(known.any(axis=0))[0]
    if len(rows) == 0 or len(cols) == 0:
        return symbols, provenance
    r0, r1 = int(rows[0]), int(rows[-1]) + 1
    c0, c1 = int(cols[0]), int(cols[-1]) + 1
    return symbols[r0:r1, c0:c1], provenance[r0:r1, c0:c1]


def to_text(symbols: np.ndarray) -> list[str]:
    return ["".join(CHAR[int(v)] for v in row) for row in symbols]


def save_grid_txt(path: str, grid: FrameGrid) -> None:
    """Save a frame grid to a readable text file (header + symbol matrix)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    header = (
        f"# source={grid.source} cell={grid.cell:.3f} "
        f"phase=({grid.phase[0]:.2f},{grid.phase[1]:.2f}) "
        f"offset=({grid.offset[0]:.1f},{grid.offset[1]:.1f}) "
        f"rows={grid.rows} cols={grid.cols}"
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(header + "\n")
        handle.write("\n".join(to_text(grid.symbols)) + "\n")


def load_grid_txt(path: str) -> tuple[dict, np.ndarray]:
    """Load a symbol matrix saved by `save_grid_txt`."""
    with open(path, encoding="utf-8") as handle:
        lines = [line.rstrip("\n") for line in handle if line.strip()]
    header = lines[0]
    rows = [line for line in lines[1:] if not line.startswith("#")]
    symbols = np.array([[CODE[ch] for ch in row] for row in rows], dtype=np.uint8)
    return {"header": header}, symbols


def save_merged_txt(
    path: str,
    symbols: np.ndarray,
    provenance: np.ndarray,
    cell: float,
    origin: tuple[float, float],
    stats: dict,
) -> None:
    """Save the merged grid with a provenance map (A/B/both per cell)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    prov_char = {0: "?", 1: "A", 2: "B", 3: "+"}
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            f"# merged cell={cell:.3f} origin=({origin[0]:.2f},{origin[1]:.2f}) "
            f"rows={symbols.shape[0]} cols={symbols.shape[1]} "
            f"both={stats['both']} only_a={stats['only_a']} only_b={stats['only_b']} "
            f"conflicts={stats['conflicts']} heads={stats['heads']}\n"
        )
        for r in range(symbols.shape[0]):
            handle.write("".join(CHAR[int(v)] for v in symbols[r]) + "\n")
        handle.write("# provenance (A=only a, B=only b, +=both)\n")
        for r in range(provenance.shape[0]):
            handle.write("".join(prov_char[int(v)] for v in provenance[r]) + "\n")


def render(symbols: np.ndarray, provenance: np.ndarray, scale: int = 18, margin: int = 8) -> np.ndarray:
    """Schematic image of the merged grid, coloured by provenance."""
    colors = {
        0: (245, 245, 245),
        1: (230, 200, 160),
        2: (160, 220, 230),
        3: (170, 230, 170),
    }
    rows, cols = symbols.shape
    image = np.full((rows * scale + 2 * margin, cols * scale + 2 * margin, 3), 255, np.uint8)
    for r in range(rows):
        for c in range(cols):
            x0 = c * scale + margin
            y0 = r * scale + margin
            cv2.rectangle(
                image,
                (x0, y0),
                (x0 + scale, y0 + scale),
                colors[min(int(provenance[r, c]), 3)],
                -1,
            )
            cv2.rectangle(image, (x0, y0), (x0 + scale, y0 + scale), (215, 215, 215), 1)
    for r in range(rows):
        for c in range(cols):
            symbol = int(symbols[r, c])
            x = c * scale + margin + scale // 2
            y = r * scale + margin + scale // 2
            if symbol == OCCUPIED:
                cv2.circle(image, (x, y), max(2, scale // 6), (110, 110, 110), -1)
            elif symbol in HEAD_VECTOR:
                dx, dy = HEAD_VECTOR[symbol]
                cv2.arrowedLine(
                    image,
                    (x, y),
                    (x + dx * scale // 3, y + dy * scale // 3),
                    (0, 0, 220),
                    2,
                    tipLength=0.45,
                )
    return image


def reference_cell(images: list[np.ndarray], fallback: float = 46.9) -> float:
    """Pick one cell size for every frame: prefer a dot-based grid, then arrows."""
    for image in images:
        try:
            grid, method = vision.detect_grid(image)
            if method == "dots":
                return float(grid.cell_w)
        except ValueError:
            continue
    cells = [vision.estimate_cell_size(image) for image in images]
    cells = [c for c in cells if c > 0]
    return float(np.median(cells)) if cells else fallback


def save_grids(grids: list[FrameGrid], outdir: str = "imgs/grids") -> None:
    """Save every frame grid as a text file and a rendered schematic image."""
    os.makedirs(outdir, exist_ok=True)
    for index, frame_grid in enumerate(grids):
        save_grid_txt(os.path.join(outdir, f"grid_{index:02d}.txt"), frame_grid)
        known = (frame_grid.symbols != UNKNOWN).astype(np.uint8) * 3
        cv2.imwrite(
            os.path.join(outdir, f"grid_{index:02d}.png"),
            render(frame_grid.symbols, known),
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Grid-level stitching: frames -> grids -> stitching"
    )
    parser.add_argument("--a", default="0", help="first frame index (or path)")
    parser.add_argument("--b", default="1", help="second frame index (or path)")
    parser.add_argument("--all", action="store_true", help="stitch every frame in the manifest")
    parser.add_argument("--capture", type=int, default=0, help="capture N fresh frames first")
    parser.add_argument("--cell", type=float, default=0.0, help="0 = auto-detect")
    parser.add_argument("--dc-min", type=int, default=6)
    parser.add_argument("--dc-max", type=int, default=20)
    parser.add_argument("--dr-max", type=int, default=6)
    parser.add_argument("--frames-dir", default="imgs/frames")
    parser.add_argument("--grids-dir", default="imgs/grids")
    parser.add_argument("--stitching-dir", default="imgs/stitching")
    parser.add_argument("--solve", action="store_true", help="run the solver on the merged grid")
    args = parser.parse_args()

    import stitch as stitch_mod

    captured = 0
    if args.capture:
        import adb_client

        adb_client.ensure_device()
        stitch_mod.capture_n(adb_client, count=args.capture, outdir=args.frames_dir)
        captured = args.capture

    all_frames = stitch_mod.load_frames(args.frames_dir)
    if args.all or (captured and captured != 2) or len(all_frames) < 2:
        frames = all_frames
    else:
        frames = [all_frames[int(args.a)], all_frames[int(args.b)]]

    images = [frame for frame, _offset in frames]
    cell = args.cell if args.cell > 0 else reference_cell(images)
    print(f"cell = {cell:.2f}px, frames = {len(frames)}")
    os.makedirs(args.stitching_dir, exist_ok=True)

    if args.all or len(frames) > 2:
        symbols, provenance, positions, cell, stats, grids = stitch_all(frames, cell=cell)
        save_grids(grids, args.grids_dir)
        print(
            f"merged: {symbols.shape[1]}x{symbols.shape[0]} cells, cells={stats['cells']} "
            f"conflicts={stats['conflicts']} ({stats['conflict_ratio'] * 100:.1f}%) "
            f"heads={stats['heads']}"
        )
        grid, occupancy, heads, _model, _trim = to_model(
            symbols, provenance, cell, grids, positions
        )
        cv2.imwrite(os.path.join(args.stitching_dir, "merged.png"), render(symbols, provenance))
        with open(os.path.join(args.stitching_dir, "merged.txt"), "w", encoding="utf-8") as handle:
            handle.write(
                f"# merged cell={cell:.3f} rows={grid.rows} cols={grid.cols} "
                f"conflicts={stats['conflicts']} heads={len(heads)} "
                f"origin=({grid.x0:.1f},{grid.y0:.1f})\n"
            )
            handle.write("\n".join(to_text(symbols)) + "\n")
        if args.solve:
            import solver

            moves = solver.playable_moves(grid, occupancy, heads)
            print(f"playable moves: {len(moves)} / {len(heads)} arrows")
            for row, col, direction in moves[:40]:
                print(f"  ({row},{col}) {direction}")
        print(f"saved grids in {args.grids_dir}/ and merged in {args.stitching_dir}/")
        return

    if len(frames) < 2:
        grid_a = extract_grid(images[0], cell, source="frame_00")
        save_grids([grid_a], args.grids_dir)
        print(f"only one frame - saved its grid in {args.grids_dir}/, nothing to stitch")
        return

    grid_a = extract_grid(images[0], cell, source="frame_00")
    grid_b = extract_grid(images[1], cell, source="frame_01")
    save_grids([grid_a, grid_b], args.grids_dir)
    for grid in (grid_a, grid_b):
        print(f"  {grid.source}: {grid.cols}x{grid.rows} cells")

    alignment = align(
        grid_a,
        grid_b,
        dc_range=range(args.dc_min, args.dc_max + 1),
        dr_range=range(-args.dr_max, args.dr_max + 1),
    )
    if alignment is None:
        raise SystemExit("Alignment failed (no translation matched well enough)")
    print(
        f"alignment: b origin in a = (dr={alignment.dr}, dc={alignment.dc}) "
        f"score={alignment.score:.3f} support={alignment.support}"
    )
    for dr, dc, score, support in alignment.alternatives:
        print(f"    candidate (dr={dr}, dc={dc}) score={score:.3f} support={support}")

    merged, provenance, stats = merge(grid_a, grid_b, alignment.offset)
    merged, provenance = trim_unknown(merged, provenance)
    print(
        f"merged: {merged.shape[1]}x{merged.shape[0]} cells, both={stats['both']} "
        f"only_a={stats['only_a']} only_b={stats['only_b']} conflicts={stats['conflicts']} "
        f"heads={stats['heads']}"
    )
    origin = (grid_a.phase[0], grid_a.phase[1])
    save_merged_txt(os.path.join(args.stitching_dir, "merged.txt"), merged, provenance, cell, origin, stats)
    cv2.imwrite(os.path.join(args.stitching_dir, "merged.png"), render(merged, provenance))
    print(f"saved grid_00/grid_01 in {args.grids_dir}/ and merged in {args.stitching_dir}/")


if __name__ == "__main__":
    main()
