"""Fast frame source for the Auto-ARROWS bot.

Uses the device `screenrecord` H.264 stream over a FIFO, decoded by OpenCV's
FFMPEG backend. This gives frames in ~15-30 ms instead of ~2400 ms for
`screencap`, and falls back to `adb_client.screenshot()` if the stream fails.
"""

from __future__ import annotations

import atexit
import os
import subprocess
import time

import cv2
import numpy as np

import adb_client

FIFO = "/tmp/autoarrows_stream.fifo"

# Fast stream enabled by default, with automatic fallback to adb_client.screenshot().
USE_STREAM = True


class Stream:
    """Persistent screenrecord -> FIFO -> OpenCV video stream."""

    def __init__(self, fifo: str = FIFO):
        self.fifo = fifo
        self.proc: subprocess.Popen | None = None
        self.cap: cv2.VideoCapture | None = None
        self.target_width = 1220
        self.target_height = 2712
        self._start()

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
            stream_w, stream_h = max(300, w // 2), max(600, h // 2)
            size_arg = f"--size {stream_w}x{stream_h}"
        except Exception:
            size_arg = "--size 610x1356"

        command = (
            f"adb exec-out screenrecord --output-format=h264 {size_arg} --time-limit 180 - > {self.fifo}"
        )
        self.proc = subprocess.Popen(command, shell=True)
        time.sleep(0.3)
        self.cap = cv2.VideoCapture(self.fifo, cv2.CAP_FFMPEG)

    def frame(self, max_drain: int = 30) -> np.ndarray | None:
        """Return the latest frame from the video stream, resized to target dimensions."""
        if self.cap is None:
            return None
        latest = None
        count = 0
        while count < max_drain:
            ok, current = self.cap.read()
            if not ok:
                if latest is not None:
                    break
                self.restart()
                if self.cap is None:
                    return None
                break
            latest = current
            count += 1

        if latest is None:
            return None

        if (latest.shape[1], latest.shape[0]) != (self.target_width, self.target_height):
            latest = cv2.resize(latest, (self.target_width, self.target_height))
        return latest

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


def get_frame(max_drain: int = 30) -> np.ndarray:
    """Return the current screen.

    Uses the fast stream when enabled (USE_STREAM), otherwise screencap (reliable
    but slower).
    """
    if not USE_STREAM:
        return adb_client.screenshot()
    stream = _get_stream()
    if stream is not None:
        try:
            frame = stream.frame(max_drain=max_drain)
            if frame is not None:
                return frame
        except Exception:
            pass
    return adb_client.screenshot()


def close() -> None:
    global _stream
    if _stream is not None:
        _stream.close()
        _stream = None


atexit.register(close)
