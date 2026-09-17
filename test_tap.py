"""Interactive tap tester.

Shows a screenshot of the phone in a window. Click anywhere on the image to send
a tap to the same point on the device, which lets us verify the coordinate
mapping between PC and phone.

Keys:
    r    refresh the screenshot
    q    quit (ESC also works)
"""

from __future__ import annotations

import cv2

import adb_client

WINDOW = "Auto-ARROWS - tap tester"
MAX_WINDOW_HEIGHT = 900


def main() -> None:
    adb_client.ensure_device()
    width, height = adb_client.screen_size()
    print(f"Device ready. Screen: {width}x{height} px")
    print("Click on the image to tap the same point on the phone.")
    print("Keys: [r] refresh  [q]/[ESC] quit")

    scale = min(1.0, MAX_WINDOW_HEIGHT / height)
    frame = adb_client.screenshot()

    def on_mouse(event: int, x: int, y: int, flags: int, param: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            device_x = int(round(x / scale))
            device_y = int(round(y / scale))
            adb_client.tap(device_x, device_y)
            print(f"tap -> ({device_x}, {device_y})")

    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WINDOW, on_mouse)

    while True:
        cv2.imshow(WINDOW, cv2.resize(frame, None, fx=scale, fy=scale))
        key = cv2.waitKey(30) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord("r"):
            frame = adb_client.screenshot()
            print("screenshot refreshed")

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
