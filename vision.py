"""Image processing helpers for the Auto-ARROWS bot."""

from __future__ import annotations

from dataclasses import dataclass, asdict

import cv2
import numpy as np


@dataclass
class Grid:
    """Mapping between logical cells and device pixel coordinates.

    Cell (row, col) has its center at (x0 + col * cell_w, y0 + row * cell_h).
    """

    x0: float
    y0: float
    cell_w: float
    cell_h: float
    cols: int
    rows: int
    pad: int = 0

    def cell_center(self, row: int, col: int) -> tuple[int, int]:
        x = int(round(self.x0 + col * self.cell_w))
        y = int(round(self.y0 + row * self.cell_h))
        return x, y

    def inside_board(self, row: int, col: int) -> bool:
        """Whether the cell belongs to the real board (excluding the padding)."""
        return self.pad <= row < self.rows - self.pad and self.pad <= col < self.cols - self.pad

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Grid":
        return cls(**data)


def to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image


def dots_mask(
    image: np.ndarray,
    gray_min: int = 200,
    gray_max: int = 245,
) -> np.ndarray:
    """Binary mask of the light-gray grid dots."""
    gray = to_gray(image)
    return ((gray > gray_min) & (gray < gray_max)).astype(np.uint8)


def foreground_mask(
    image: np.ndarray,
    dark_max: int = 200,
    sat_min: int = 40,
) -> np.ndarray:
    """Mask of arrow pixels: dark strokes or saturated (colored) strokes.

    Excludes the white background and the light-gray grid dots, so colored
    arrows are captured as well as the black ones.
    """
    if image.ndim == 2:
        return (image < dark_max).astype(np.uint8)
    blue, green, red = cv2.split(image.astype(np.int16))
    gray = 0.114 * blue + 0.587 * green + 0.299 * red
    maximum = np.maximum(np.maximum(blue, green), red)
    minimum = np.minimum(np.minimum(blue, green), red)
    saturation = maximum - minimum
    return ((gray < dark_max) | (saturation > sat_min)).astype(np.uint8)


def detect_dots(
    image: np.ndarray,
    gray_min: int = 200,
    gray_max: int = 245,
    min_area: int = 60,
    max_area: int = 350,
    min_size: int = 10,
    max_size: int = 22,
) -> list[tuple[float, float]]:
    """Detect the light-gray grid dots and return their centers."""
    mask = dots_mask(image, gray_min, gray_max)
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    dots: list[tuple[float, float]] = []
    for i in range(1, count):
        _x, _y, w, h, area = stats[i]
        if (
            min_area <= area <= max_area
            and min_size <= w <= max_size
            and min_size <= h <= max_size
        ):
            dots.append((float(centroids[i][0]), float(centroids[i][1])))
    return dots


def nearest_neighbor_spacing(dots: list[tuple[float, float]]) -> float:
    """Coarse cell size as the median nearest-neighbor distance between dots."""
    arr = np.asarray(dots, dtype=float)
    if len(arr) < 2:
        return 0.0
    diff = arr[:, None, :] - arr[None, :, :]
    dist = np.sqrt((diff ** 2).sum(-1))
    np.fill_diagonal(dist, np.inf)
    return float(np.median(dist.min(axis=1)))


def _refine_lattice(values: list[float], coarse: float) -> tuple[float, float]:
    """Fit value = phase + k * spacing, returning (phase in [0, spacing), spacing)."""
    if coarse <= 0 or len(values) < 3:
        return 0.0, float(coarse)
    v = np.array(values, dtype=float)
    spacing = float(coarse)
    phase = 0.0
    for _ in range(3):
        k = np.round((v - v.min()) / spacing)
        design = np.vstack([np.ones_like(k), k]).T
        coef, *_ = np.linalg.lstsq(design, v, rcond=None)
        phase, spacing = float(coef[0]), float(coef[1])
        residual = np.abs(v - (phase + k * spacing))
        keep = residual < spacing / 3
        if keep.all() or spacing <= 0:
            break
        v = v[keep]
    phase = phase % spacing if spacing else 0.0
    return phase, spacing


def estimate_lattice(
    dots: list[tuple[float, float]],
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return ((x0, y0), (cell_w, cell_h)) refined from the detected dots."""
    coarse = nearest_neighbor_spacing(dots)
    x0, cell_w = _refine_lattice([x for x, _ in dots], coarse)
    y0, cell_h = _refine_lattice([y for _, y in dots], coarse)
    return (x0, y0), (cell_w, cell_h)


def detect_board_box(
    image: np.ndarray,
    dark_max: int = 60,
    top_margin: int = 400,
    bottom_margin: int = 300,
) -> tuple[int, int, int, int] | None:
    """Approximate board box (x_min, y_min, x_max, y_max) from the dark arrows."""
    gray = to_gray(image)
    height = gray.shape[0]
    dark = gray < dark_max
    dark[:top_margin, :] = False
    dark[height - bottom_margin :, :] = False
    ys, xs = np.where(dark)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _align_origin(phase: float, cell: float, lower: float) -> float:
    """Return the lattice position closest to the center of the first cell."""
    target = lower + cell / 2
    return phase + round((target - phase) / cell) * cell


def build_grid(
    dots: list[tuple[float, float]],
    board: tuple[int, int, int, int],
    pad: int = 2,
) -> Grid:
    """Build a Grid from detected dots and board box (x_min, y_min, x_max, y_max).

    The grid is extended by `pad` cells on each side so that arrows close to the
    board margin are not left outside the grid (avoids false negatives).
    """
    (x_phase, y_phase), (cell_w, cell_h) = estimate_lattice(dots)
    if cell_w <= 0 or cell_h <= 0:
        raise ValueError("Could not estimate cell spacing (no dots detected?)")
    x_min, y_min, x_max, y_max = board
    x0 = _align_origin(x_phase, cell_w, x_min) - pad * cell_w
    y0 = _align_origin(y_phase, cell_h, y_min) - pad * cell_h
    cols = int(np.ceil((x_max - x_min) / cell_w)) + 2 * pad
    rows = int(np.ceil((y_max - y_min) / cell_h)) + 2 * pad
    return Grid(x0=x0, y0=y0, cell_w=cell_w, cell_h=cell_h, cols=cols, rows=rows, pad=pad)


def _period_score(projection: np.ndarray, max_lag: int = 220) -> tuple[float, float]:
    """Dominant period of a 1D projection via autocorrelation, with its score."""
    signal = projection.astype(np.float32)
    signal = signal - signal.mean()
    if not np.any(signal):
        return 0.0, -1.0
    autocorr = np.correlate(signal, signal, "full")[len(signal) - 1 :]
    autocorr = autocorr / autocorr[0]
    limit = min(max_lag, len(autocorr))
    best, best_score = 0.0, -1.0
    for candidate in range(5, max(6, limit // 2)):
        score = sum(
            autocorr[harmonic * candidate]
            for harmonic in (1, 2, 3)
            if harmonic * candidate < limit
        )
        if score > best_score:
            best_score, best = score, float(candidate)
    return best, float(best_score)


def _fundamental_period(projection: np.ndarray, max_lag: int = 220) -> float:
    """Dominant period of a 1D projection via autocorrelation."""
    return _period_score(projection, max_lag)[0]


def estimate_cell_size(image: np.ndarray, max_lag: int = 220, min_cell: float = 15.0) -> float:
    """Estimate the (square) cell size from the arrows themselves.

    The arrows sit on the grid, so the periodic spacing of the arrow foreground
    reveals the cell size even when there are no gray dots (full board).

    The horizontal and vertical periods are combined only when they agree; if one
    axis has no detectable periodicity (a garbage small period), the other axis is
    trusted instead of being averaged with it (which would halve the cell).
    """
    mask = foreground_mask(image).astype(np.float32)
    candidates = [
        _period_score(mask.sum(axis=0), max_lag),
        _period_score(mask.sum(axis=1), max_lag),
    ]
    candidates = [(p, s) for p, s in candidates if p >= min_cell]
    if not candidates:
        return 0.0
    if len(candidates) == 1:
        return float(candidates[0][0])
    (p1, s1), (p2, s2) = candidates
    if abs(p1 - p2) <= 0.2 * max(p1, p2):
        return float((p1 + p2) / 2)
    return float(p1 if s1 >= s2 else p2)


def _best_phase(projection: np.ndarray, cell: float) -> float:
    """Phase (lattice origin) that best aligns with the projection peaks."""
    size = len(projection)
    if cell <= 0:
        return 0.0
    best, best_phase = -1.0, 0.0
    for phase in range(int(round(cell))):
        total = 0.0
        index = int(round(phase))
        while index < size:
            total += projection[index]
            index += int(round(cell))
        if total > best:
            best, best_phase = total, float(phase)
    return best_phase


def estimate_phase(image: np.ndarray, cell: float) -> tuple[float, float]:
    """Estimate the lattice phase (a cell centre) from the arrow projection."""
    mask = foreground_mask(image).astype(np.float32)
    return _best_phase(mask.sum(axis=0), cell), _best_phase(mask.sum(axis=1), cell)


def board_box_foreground(
    image: np.ndarray,
    top_margin: int = 400,
    bottom_margin: int = 300,
) -> tuple[int, int, int, int] | None:
    """Board bounding box from the arrow foreground (inside the UI margins)."""
    mask = foreground_mask(image).astype(bool)
    height = mask.shape[0]
    mask[:top_margin, :] = False
    mask[height - bottom_margin :, :] = False
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def build_grid_arrows(image: np.ndarray, pad: int = 2) -> Grid:
    """Build the Grid from the arrows alone (cell size + phase), no dots needed."""
    cell = estimate_cell_size(image)
    if cell <= 0:
        raise ValueError("Could not estimate the cell size from the arrows")
    x_phase, y_phase = estimate_phase(image, cell)
    box = board_box_foreground(image)
    if box is None:
        raise ValueError("Could not find the board (no arrows visible?)")
    x_min, y_min, x_max, y_max = box
    x0 = _align_origin(x_phase, cell, x_min) - pad * cell
    y0 = _align_origin(y_phase, cell, y_min) - pad * cell
    cols = int(np.ceil((x_max - x_min) / cell)) + 2 * pad
    rows = int(np.ceil((y_max - y_min) / cell)) + 2 * pad
    return Grid(x0=x0, y0=y0, cell_w=cell, cell_h=cell, cols=cols, rows=rows, pad=pad)


def detect_grid(image: np.ndarray, pad: int = 2) -> tuple["Grid", str]:
    """Detect the grid, preferring gray dots and falling back to the arrows.

    Returns (grid, method) where method is 'dots' or 'arrows'.
    """
    box = board_box_foreground(image)
    dots = detect_dots(image)
    if box is not None and len(dots) >= 5:
        inside = [d for d in dots if box[0] <= d[0] <= box[2] and box[1] <= d[1] <= box[3]]
        if len(inside) >= 5:
            try:
                return build_grid(inside, box, pad=pad), "dots"
            except ValueError:
                pass
    return build_grid_arrows(image, pad=pad), "arrows"


def draw_grid(image: np.ndarray, grid: Grid, color: tuple[int, int, int] = (0, 0, 255)) -> np.ndarray:
    """Return a copy of the image with the grid cell centers overlaid."""
    canvas = image.copy()
    for row in range(grid.rows):
        for col in range(grid.cols):
            x, y = grid.cell_center(row, col)
            cv2.circle(canvas, (x, y), 3, color, -1)
    return canvas


_DIRECTION_VECTORS = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}


def _triangle_template(direction: str, size: int) -> np.ndarray:
    """Filled triangle template pointing in the given direction."""
    lo = int(round(size * 0.09))
    hi = int(round(size * 0.91))
    mid = size // 2
    corners = {
        "R": [[lo, lo], [lo, hi], [hi, mid]],
        "L": [[hi, lo], [hi, hi], [lo, mid]],
        "D": [[lo, lo], [hi, lo], [mid, hi]],
        "U": [[lo, hi], [hi, hi], [mid, lo]],
    }[direction]
    template = np.zeros((size, size), np.float32)
    cv2.fillPoly(template, [np.array(corners, np.int32)], 1.0)
    return template


def _head_direction(black: np.ndarray, cx: int, cy: int, size: int, min_score: float) -> str | None:
    half = 32
    # Pad the image so that heads near the border still get a full window (a raw
    # slice with a negative index, e.g. cx-32 < 0, would silently be empty).
    padded = cv2.copyMakeBorder(black, half, half, half, half, cv2.BORDER_CONSTANT, value=0)
    window = padded[cy : cy + 2 * half, cx : cx + 2 * half]
    if window.shape[0] < size or window.shape[1] < size:
        return None
    best, best_score = None, min_score
    for direction in "RLDU":
        template = _triangle_template(direction, size)
        result = cv2.matchTemplate(
            window.astype(np.float32), template, cv2.TM_CCORR_NORMED, mask=template
        )
        score = float(np.nanmax(result))
        if score > best_score:
            best_score, best = score, direction
    return best


def detect_head_pixels(
    image: np.ndarray,
    cell_w: float,
    dark_max: int = 200,
    head_ratio: float = 0.16,
    min_score: float = 0.9,
) -> list[tuple[int, int, str]]:
    """Detect arrowheads and return (x, y, direction) in screen pixels.

    Heads are located by the distance transform (a triangle is thicker than the
    body stroke) and oriented with a triangle template match. Colored arrows are
    handled by using a saturation-aware foreground mask.
    """
    black = foreground_mask(image, dark_max=dark_max)
    distance = cv2.distanceTransform(black, cv2.DIST_L2, 5)
    window = int(round(cell_w * 0.4)) | 1
    local_max = cv2.dilate(distance, np.ones((window, window), np.uint8))
    peaks = (distance == local_max) & (distance > head_ratio * cell_w)
    size = int(round(cell_w * 0.7))
    results: list[tuple[int, int, str]] = []
    seen: set[tuple[int, int]] = set()
    for y, x in zip(*np.where(peaks)):
        key = (int(x) // int(cell_w), int(y) // int(cell_w))
        if key in seen:
            continue
        direction = _head_direction(black, int(x), int(y), size, min_score)
        if direction is not None:
            results.append((int(x), int(y), direction))
            seen.add(key)
    return results


def detect_arrowheads(
    image: np.ndarray,
    grid: Grid,
    dark_max: int = 200,
    head_ratio: float = 0.16,
    min_score: float = 0.9,
) -> dict[tuple[int, int], str]:
    """Detect arrowheads and return {(row, col): direction} for the given grid."""
    heads: dict[tuple[int, int], str] = {}
    for x, y, direction in detect_head_pixels(
        image, grid.cell_w, dark_max=dark_max, head_ratio=head_ratio, min_score=min_score
    ):
        row = int(round((y - grid.y0) / grid.cell_h))
        col = int(round((x - grid.x0) / grid.cell_w))
        if 0 <= row < grid.rows and 0 <= col < grid.cols:
            heads.setdefault((row, col), direction)
    return heads


def draw_heads(
    image: np.ndarray,
    grid: Grid,
    heads: dict[tuple[int, int], str],
    color: tuple[int, int, int] = (255, 0, 0),
) -> np.ndarray:
    """Return a copy of the image with detected arrowheads marked."""
    canvas = image.copy()
    for (row, col), direction in heads.items():
        x, y = grid.cell_center(row, col)
        cv2.circle(canvas, (x, y), 6, color, -1)
        dx, dy = _DIRECTION_VECTORS[direction]
        cv2.arrowedLine(canvas, (x, y), (x + dx * 20, y + dy * 20), color, 3, tipLength=0.4)
    return canvas


EMPTY = 0
OCCUPIED = 1
UNKNOWN = 2


def cell_bounds(grid: Grid, row: int, col: int) -> tuple[int, int, int, int]:
    """Pixel bounds (x0, y0, x1, y1) of a grid cell."""
    cx, cy = grid.cell_center(row, col)
    return (
        int(round(cx - grid.cell_w / 2)),
        int(round(cy - grid.cell_h / 2)),
        int(round(cx + grid.cell_w / 2)),
        int(round(cy + grid.cell_h / 2)),
    )


def build_occupancy(
    image: np.ndarray,
    grid: Grid,
    occupied_ratio: float = 0.08,
    center_ratio: float = 0.3,
) -> np.ndarray:
    """Classify every grid cell as EMPTY, OCCUPIED or UNKNOWN.

    A cell is OCCUPIED when either enough of its area is arrow foreground, or the
    arrow passes through its centre. The second test matters for an arrow's tail:
    the line ends at the cell centre and only covers half the cell (about 0.05 to
    0.11), which the area test alone would miss. The grid dots are excluded by the
    foreground mask, so an empty cell has no foreground at all.
    Cells that are not fully on screen are UNKNOWN (we have no pixels for them).
    """
    mask = foreground_mask(image)
    height, width = mask.shape[:2]
    half = max(1, int(round(min(grid.cell_w, grid.cell_h) * 0.18)))
    occupancy = np.full((grid.rows, grid.cols), EMPTY, dtype=np.uint8)
    for row in range(grid.rows):
        for col in range(grid.cols):
            x0, y0, x1, y1 = cell_bounds(grid, row, col)
            if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
                occupancy[row, col] = UNKNOWN
                continue
            ratio = float(mask[y0:y1, x0:x1].mean())
            if ratio >= occupied_ratio:
                occupancy[row, col] = OCCUPIED
                continue
            cx, cy = grid.cell_center(row, col)
            center = mask[cy - half : cy + half, cx - half : cx + half]
            occupancy[row, col] = (
                OCCUPIED if center.size and float(center.mean()) >= center_ratio else EMPTY
            )
    return occupancy


def draw_occupancy(image: np.ndarray, grid: Grid, occupancy: np.ndarray) -> np.ndarray:
    """Return a copy of the image with occupied (orange) and unknown (magenta) cells."""
    canvas = image.copy()
    overlay = canvas.copy()
    for row in range(grid.rows):
        for col in range(grid.cols):
            value = occupancy[row, col]
            if value == EMPTY:
                continue
            x0, y0, x1, y1 = cell_bounds(grid, row, col)
            color = (0, 165, 255) if value == OCCUPIED else (255, 0, 255)
            cv2.rectangle(overlay, (x0, y0), (x1, y1), color, -1)
    cv2.addWeighted(overlay, 0.35, canvas, 0.65, 0, canvas)
    return canvas


def estimate_shift(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    top_margin: int = 400,
    bottom_margin: int = 300,
) -> tuple[float, float, float]:
    """Estimate the pixel shift (dx, dy) of frame_b relative to frame_a.

    Uses phase correlation on the board area (UI margins excluded). Returns the
    shift and the correlation response (higher is better).
    """
    gray_a = to_gray(frame_a).astype(np.float32)
    gray_b = to_gray(frame_b).astype(np.float32)
    height = gray_a.shape[0]
    region = slice(top_margin, height - bottom_margin)
    window = cv2.createHanningWindow((gray_a.shape[1], region.stop - region.start), cv2.CV_32F)
    (dx, dy), response = cv2.phaseCorrelate(gray_a[region], gray_b[region], window)
    return float(dx), float(dy), float(response)


def load_tap_mask(path: str = "imgs/mask/mask.png") -> np.ndarray:
    """Load the tap mask: True = allowed (blue), False = forbidden (UI).

    The mask is a screenshot-sized image where the board area is blue and the
    UI (top bar, hint button) is red. Raises if the file is missing, so the bot
    never taps without a valid mask.
    """
    image = cv2.imread(path)
    if image is None:
        raise FileNotFoundError(f"tap mask not found: {path}")
    return image[:, :, 0].astype(np.int16) > image[:, :, 2].astype(np.int16)


def tap_allowed(mask: np.ndarray | None, x: int, y: int) -> bool:
    """Whether a screen tap at (x, y) is inside the allowed (blue) area."""
    if mask is None:
        return True
    if not (0 <= x < mask.shape[1] and 0 <= y < mask.shape[0]):
        return False
    return bool(mask[y, x])
