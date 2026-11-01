"""Takes one picture in its own process and writes a JPEG to standard output.

A separate process means a camera driver or capture crash cannot take ALPHA down,
and the camera is released the moment the picture is taken.
"""

from __future__ import annotations

import sys

LONGEST_SIDE = 1280
JPEG_QUALITY = 80
READING_QUALITY = 92
CAMERA_WARM_UP_FRAMES = 8


def grab_screen():
    import mss
    import numpy

    with mss.MSS() as screens:
        return numpy.array(screens.grab(screens.monitors[1]))[:, :, :3]


def grab_camera():
    import cv2

    camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    try:
        if not camera.isOpened():
            raise RuntimeError("no camera")
        frame = None
        for _ in range(CAMERA_WARM_UP_FRAMES):
            ok, frame = camera.read()
            if not ok:
                raise RuntimeError("the camera gave no picture")
        return frame
    finally:
        camera.release()


def main(source: str) -> None:
    import cv2

    reading = source == "reading"  # Small text stays sharp when a screen is to be read word by word.
    picture = grab_screen() if source in ("screen", "reading") else grab_camera()
    height, width = picture.shape[:2]
    scale = LONGEST_SIDE / max(height, width)
    if scale < 1 and not reading:
        picture = cv2.resize(picture, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, READING_QUALITY if reading else JPEG_QUALITY])
    if not ok:
        raise RuntimeError("the picture could not be encoded")
    sys.stdout.buffer.write(encoded.tobytes())
    sys.stdout.flush()


if __name__ == "__main__":
    main(sys.argv[1])
