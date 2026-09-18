"""Low-level ADB interface for the Auto-ARROWS bot."""

from __future__ import annotations

import atexit
import subprocess

import cv2
import numpy as np


class AdbError(RuntimeError):
    """Raised when an ADB command fails or the device is not usable."""


_shell_proc: subprocess.Popen | None = None


def _get_shell() -> subprocess.Popen:
    global _shell_proc
    if _shell_proc is None or _shell_proc.poll() is not None:
        try:
            _shell_proc = subprocess.Popen(
                ["adb", "shell"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except Exception as error:
            raise AdbError(f"Failed to start persistent ADB shell: {error}") from error
    return _shell_proc


def _close_shell() -> None:
    global _shell_proc
    if _shell_proc is not None:
        try:
            _shell_proc.terminate()
        except Exception:
            pass
        _shell_proc = None


atexit.register(_close_shell)


def _adb(*args: str) -> bytes:
    result = subprocess.run(
        ["adb", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode(errors="replace").strip()
        raise AdbError(message or f"adb {' '.join(args)} failed")
    return result.stdout


def list_devices() -> list[str]:
    """Return the raw `adb devices` lines (excluding the header)."""
    output = _adb("devices").decode()
    return [line.strip() for line in output.splitlines()[1:] if line.strip()]


def ensure_device() -> None:
    """Raise AdbError unless exactly one device is connected and authorized."""
    devices = list_devices()
    if not devices:
        raise AdbError("No device connected. Check the USB cable and USB debugging.")
    if not any(line.endswith("\tdevice") or line.endswith(" device") for line in devices):
        raise AdbError("Device not ready:\n" + "\n".join(devices))


def screen_size() -> tuple[int, int]:
    """Return the physical screen size as (width, height)."""
    output = _adb("shell", "wm", "size").decode()
    for line in output.splitlines():
        if "size:" in line:
            value = line.split(":", 1)[1].strip()
            width, height = value.split("x")
            return int(width), int(height)
    raise AdbError(f"Could not parse screen size:\n{output}")


def tap(x: int, y: int) -> None:
    """Tap the screen at device pixel coordinates (x, y)."""
    try:
        shell = _get_shell()
        shell.stdin.write(f"input tap {x} {y}\n")
        shell.stdin.flush()
    except Exception:
        _adb("shell", "input", "tap", str(x), str(y))


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
    """Swipe from (x1, y1) to (x2, y2) over duration_ms milliseconds."""
    try:
        shell = _get_shell()
        shell.stdin.write(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}\n")
        shell.stdin.flush()
    except Exception:
        _adb(
            "shell",
            "input",
            "swipe",
            str(x1),
            str(y1),
            str(x2),
            str(y2),
            str(duration_ms),
        )


def screenshot_bytes() -> bytes:
    """Capture a PNG screenshot of the device screen."""
    return _adb("exec-out", "screencap", "-p")


def screenshot() -> np.ndarray:
    """Capture a screenshot and decode it as a BGR image."""
    data = np.frombuffer(screenshot_bytes(), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise AdbError("Failed to decode screenshot")
    return image


def save_screenshot(path: str) -> None:
    """Capture a screenshot and write the PNG to disk."""
    with open(path, "wb") as file:
        file.write(screenshot_bytes())
