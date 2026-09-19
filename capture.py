"""Fast frame source for the Auto-ARROWS bot.

Uses the device `screenrecord` H.264 stream over a FIFO, decoded by OpenCV's
FFMPEG backend. This gives frames in ~15-30 ms instead of ~2400 ms for
`screencap`, and falls back to `adb_client.screenshot()` if the stream fails
or the latest frame is too old (max_age guard).

The key fix over the old implementation: a daemon thread drains the H.264 FIFO
continuously, so the pipe never accumulates and the last decoded frame is always
the most recent one. `frame()` just reads the cached result under a lock.
"""

from __future__ import annotations

import atexit
import os
import subprocess
import threading
import time

import cv2
import numpy as np

import adb_client

FIFO = "/tmp/autoarrows_stream.fifo"

# Master switch kept for safety — set to True to allow the fast H.264 path.
# The game-decision paths (bot.py, play_grid.py) always use screencap via
# prefer_stream=False (the default).  Only the stitching scan passes
# prefer_stream=True to opt into the fast path.
USE_STREAM = True


class Stream:
    """Persistent screenrecord -> FIFO -> OpenCV video stream.

    A daemon thread drains the FIFO continuously so it never fills up.
    get_frame() / frame() read the last decoded frame from memory (sub-ms),
    protected by a lock.  A freshness guard (max_age) ensures stale frames
    fall back to screencap automatically.
    """

    def __init__(self, fifo: str = FIFO):
        self.fifo = fifo
        self.proc: subprocess.Popen | None = None
        self.cap: cv2.VideoCapture | None = None
        self.target_width = 1220
        self.target_height = 2712

        # Shared state between drain thread and callers
        self._latest: np.ndarray | None = None
        self._latest_ts: float = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._start()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _start(self) -> None:
        if os.path.exists(self.fifo):
            try:
                os.remove(self.fifo)
            except OSError:
                pass
        os.makedirs(os.path.dirname(self.fifo) or ".", exist_ok=True)
        os.mkfifo(self.fifo)

        try:
            w, h = adb_client.screen_size()
            self.target_width, self.target_height = w, h
        except Exception:
            self.target_width, self.target_height = 1220, 2712

        command = (
            f"adb exec-out screenrecord --output-format=h264 --time-limit 180 - > {self.fifo}"
        )
        self.proc = subprocess.Popen(command, shell=True)
        time.sleep(0.3)
        self.cap = cv2.VideoCapture(self.fifo, cv2.CAP_FFMPEG)

        # Reset shared state and (re)start the drain thread
        self._stop.clear()
        with self._lock:
            self._latest = None
            self._latest_ts = 0.0
        self._thread = threading.Thread(target=self._drain_loop, daemon=True, name="stream-drain")
        self._thread.start()

    def _drain_loop(self) -> None:
        """Background thread: read frames as fast as the encoder produces them.

        This keeps the FIFO empty so the encoder never stalls and the cached
        frame is always the most recently produced one.
        """
        while not self._stop.is_set():
            if self.cap is None:
                time.sleep(0.01)
                continue
            ok, current = self.cap.read()
            if not ok:
                time.sleep(0.005)
                continue
            if (current.shape[1], current.shape[0]) != (self.target_width, self.target_height):
                current = cv2.resize(current, (self.target_width, self.target_height))
            with self._lock:
                self._latest = current
                self._latest_ts = time.monotonic()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def frame(self, max_age: float = 0.2) -> np.ndarray | None:
        """Return the most recent decoded frame if it is younger than max_age seconds.

        Returns None when:
        - The drain thread has not decoded any frame yet.
        - The latest frame is older than max_age (e.g. encoder stalled).
        The caller should fall back to adb_client.screenshot() in these cases.
        """
        with self._lock:
            if self._latest is None:
                return None
            age = time.monotonic() - self._latest_ts
            if age > max_age:
                return None
            return self._latest.copy()

    def restart(self) -> None:
        self.close()
        self._start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        if self.proc is not None:
            self.proc.terminate()
            self.proc = None
        subprocess.run(["pkill", "-f", "screenrecord"], capture_output=True)
        if os.path.exists(self.fifo):
            try:
                os.remove(self.fifo)
            except OSError:
                pass
        with self._lock:
            self._latest = None
            self._latest_ts = 0.0


_stream: Stream | None = None


def _get_stream() -> Stream | None:
    global _stream
    if _stream is None:
        try:
            _stream = Stream()
            # Give the drain thread a moment to capture the first frame
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if _stream.frame(max_age=5.0) is not None:
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("stream produced no frame within 3 s")
        except Exception:
            if _stream is not None:
                _stream.close()
            _stream = None
    return _stream


def get_frame(
    prefer_stream: bool = False,
    max_age: float = 0.2,
) -> np.ndarray:
    """Return the current screen frame.

    Parameters
    ----------
    prefer_stream:
        When True, attempt to use the fast H.264 stream (suitable for
        stitching / varrimento, where a frame that is tens of ms old is
        perfectly acceptable).  Falls back silently to screencap if the
        stream is unavailable or the latest frame exceeds max_age.
        When False (the default), always use screencap for 100% freshness
        — required for game-decision code (bot.py, play_grid.py) where a
        stale frame could cause an incorrect tap and a penalty.
    max_age:
        Maximum acceptable age (seconds) of the cached frame when using
        the stream path.  Frames older than this trigger the screencap
        fallback.
    """
    if prefer_stream and USE_STREAM:
        stream = _get_stream()
        if stream is not None:
            try:
                cached = stream.frame(max_age=max_age)
                if cached is not None:
                    return cached
            except Exception:
                pass
    return adb_client.screenshot()


def close() -> None:
    global _stream
    if _stream is not None:
        _stream.close()
        _stream = None


atexit.register(close)
