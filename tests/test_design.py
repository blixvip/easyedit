"""The shipped picture-to-design function, from its real start state."""
import unittest

import cv2
import numpy as np

from easyedit.design import design_picture


def _png() -> bytes:
    image = np.zeros((96, 140, 3), np.uint8)
    image[:] = (30, 50, 190)
    cv2.rectangle(image, (12, 18), (70, 78), (20, 180, 40), -1)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("could not build the png fixture")
    return encoded.tobytes()


def _jpeg() -> bytes:
    image = np.zeros((110, 160, 3), np.uint8)
    image[:] = (210, 120, 30)
    cv2.circle(image, (80, 55), 28, (40, 40, 220), -1)
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise RuntimeError("could not build the jpeg fixture")
    return encoded.tobytes()


def _as_image(data: bytes):
    decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None or decoded.size == 0:
        raise AssertionError("design was not an image")
    return decoded


class DesignPictureTest(unittest.TestCase):
    def test_png_and_jpeg_become_different_designs(self):
        png = _png()
        jpeg = _jpeg()
        designed_png = design_picture(png)
        designed_jpeg = design_picture(jpeg)
        self.assertTrue(designed_png)
        self.assertTrue(designed_jpeg)
        self.assertNotEqual(designed_png, png)
        self.assertNotEqual(designed_jpeg, jpeg)
        self.assertNotEqual(designed_png, designed_jpeg)
        self.assertGreater(_as_image(designed_png).shape[0], 0)
        self.assertGreater(_as_image(designed_jpeg).shape[1], 0)


if __name__ == "__main__":
    unittest.main()
