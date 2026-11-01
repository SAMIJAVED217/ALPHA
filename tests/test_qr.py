import cv2
import numpy as np
import pytest

from alpha import qr


def read_back(grid, scale=8, quiet=4):
    picture = np.array(grid, dtype=np.uint8) ^ 1
    picture = np.pad(picture, quiet, constant_values=1) * 255
    picture = cv2.resize(picture, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    return cv2.QRCodeDetector().detectAndDecode(picture)[0]


@pytest.mark.parametrize(
    "link",
    [
        "hi",
        "http://192.168.1.20:8765/#123456",
        "http://10.0.0.5:8765/#000000",
        "http://172.16.254.100:8765/#987654",
        "http://100.101.102.103:8765/#555555",
        "x" * 17,
        "x" * 18,
        "x" * 40,
        "x" * 70,
        "x" * 100,
    ],
)
def test_a_real_qr_reader_gets_the_same_text_back(link):
    assert read_back(qr.make(link)) == link


def test_every_pairing_style_link_reads_back():
    for code in range(0, 1_000_000, 83_333):
        link = f"http://192.168.0.{code % 250 + 1}:8765/#{code:06d}"
        assert read_back(qr.make(link)) == link


def test_the_size_grows_with_the_text():
    assert len(qr.make("hi")) == 21
    assert len(qr.make("x" * 40)) == 29


def test_text_too_long_is_refused():
    with pytest.raises(ValueError):
        qr.make("x" * 107)
