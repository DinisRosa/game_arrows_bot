"""Fast frame source for the Auto-ARROWS bot.

Uses the device `screenrecord` H.264 stream over a FIFO, decoded by OpenCV's
FFMPEG backend. This gives frames in ~5-15 ms instead of ~2400 ms for
`screencap`, and falls back to `adb_client.screenshot()` if the stream fails.
"""

from __future__ import annotations

import atexit
import os
import subprocess

import cv2
import numpy as np

import adb_client

FIFO = "/tmp/opencode/autoarrows.fifo"

# The screenrecord->FIFO->OpenCV stream proved unreliable (it can return a frozen
# frame), so it is disabled by default and screencap is used instead.
USE_STREAM = False


class Stream:
    """Persistent screenrecord -> FIFO -> OpenCV video stream."""

    def __init__(self, fifo: str = FIFO):
        self.fifo = fifo
        self.proc: subprocess.Popen | None = None
        self.cap: cv2.VideoCapture | None = None
        self._start()

    def _start(self) -> None:
        if os.path.exists(self.fifo):
            os.remove(self.fifo)
        os.mkfifo(self.fifo)
        command = (
            f"adb exec-out screenrecord --output-format=h264 --time-limit 180 - > {self.fifo}"
        )
        self.proc = subprocess.Popen(command, shell=True)
        self.cap = cv2.VideoCapture(self.fifo, cv2.CAP_FFMPEG)

    def frame(self, max_drain: int = 200, stable_diff: float = 0.5) -> np.ndarray | None:
        """Read until the frame stabilises, returning the current screen.

        The stream buffers frames faster than we consume them, so reading a fixed
        number is not enough; we drain until two consecutive frames match.
        """
        if self.cap is None:
            return None
        previous = None
        frame = None
        for _ in range(max_drain):
            ok, current = self.cap.read()
            if not ok:
                self.restart()
                if self.cap is None:
                    return None
                continue
            frame = current
            if previous is not None:
                diff = float(
                    np.abs(current.astype(np.int16) - previous.astype(np.int16)).mean()
                )
                if diff < stable_diff:
                    return current
            previous = current
        return frame

    def restart(self) -> None:
        self.close()
        self._start()

    def close(self) -> None:
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


_stream: Stream | None = None


def _get_stream() -> Stream | None:
    global _stream
    if _stream is None:
        try:
            _stream = Stream()
            if _stream.frame() is None:
                raise RuntimeError("stream produced no frame")
        except Exception:
            if _stream is not None:
                _stream.close()
            _stream = None
    return _stream


def get_frame(max_drain: int = 200) -> np.ndarray:
    """Return the current screen.

    Uses the fast stream when enabled (USE_STREAM), otherwise screencap (reliable
    but slower).
    """
    if not USE_STREAM:
        return adb_client.screenshot()
    stream = _get_stream()
    if stream is not None:
        frame = stream.frame(max_drain=max_drain)
        if frame is not None:
            return frame
    return adb_client.screenshot()


def close() -> None:
    global _stream
    if _stream is not None:
        _stream.close()
        _stream = None


atexit.register(close)
