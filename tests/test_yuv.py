"""Unit tests for HomBot planar YUV conversion."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from lg_hombot.yuv import (
    CHROMA_PLANE_SIZE,
    FRAME_BYTES,
    FRAME_HEIGHT,
    FRAME_WIDTH,
    U_PLANE_OFFSET,
    V_PLANE_OFFSET,
    Y_PLANE_SIZE,
    clamp_uint8,
    validate_yuv_frame,
    yuv422_planar_to_jpeg,
    yuv422_planar_to_rgb,
)


def _blank_frame() -> bytearray:
    data = bytearray(FRAME_BYTES)
    # Neutral chroma keeps luminance colors roughly intact.
    for i in range(CHROMA_PLANE_SIZE):
        data[U_PLANE_OFFSET + i] = 128
        data[V_PLANE_OFFSET + i] = 128
    return data


def test_plane_layout_constants() -> None:
    assert FRAME_WIDTH == 320
    assert FRAME_HEIGHT == 240
    assert FRAME_BYTES == 153600
    assert Y_PLANE_SIZE == 76800
    assert U_PLANE_OFFSET == 76800
    assert V_PLANE_OFFSET == 115200
    assert CHROMA_PLANE_SIZE == 38400


def test_validate_exact_frame_size() -> None:
    validate_yuv_frame(bytes(FRAME_BYTES))
    with pytest.raises(ValueError, match="expected 153600"):
        validate_yuv_frame(bytes(FRAME_BYTES - 1))
    with pytest.raises(ValueError, match="expected 153600"):
        validate_yuv_frame(bytes(FRAME_BYTES + 1))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-1.0, 0),
        (0.0, 0),
        (0.4, 0),
        (0.5, 0),  # round half to even
        (0.6, 1),
        (1.5, 2),  # round half to even
        (254.5, 254),
        (255.0, 255),
        (300.0, 255),
        (float("nan"), 0),
    ],
)
def test_clamp_uint8(value: float, expected: int) -> None:
    assert clamp_uint8(value) == expected


def test_neutral_chroma_preserves_luma() -> None:
    frame = _blank_frame()
    frame[0] = 100
    frame[1] = 120
    rgb = yuv422_planar_to_rgb(frame)
    assert rgb[0:3] == bytes((100, 100, 100))
    assert rgb[3:6] == bytes((120, 120, 120))


def test_shared_chroma_for_pixel_pair() -> None:
    frame = _blank_frame()
    frame[0] = 80
    frame[1] = 90
    # First chroma sample: U and V for pixels 0 and 1.
    frame[U_PLANE_OFFSET] = 156
    frame[V_PLANE_OFFSET] = 104

    rgb = yuv422_planar_to_rgb(frame)

    def expected(y: int) -> tuple[int, int, int]:
        return (
            clamp_uint8(y + 1.371 * (104 - 128)),
            clamp_uint8(y - 0.336 * (156 - 128) - 0.698 * (104 - 128)),
            clamp_uint8(y + 1.732 * (156 - 128)),
        )

    assert tuple(rgb[0:3]) == expected(80)
    assert tuple(rgb[3:6]) == expected(90)


def test_jpeg_output_dimensions() -> None:
    frame = _blank_frame()
    for y in range(Y_PLANE_SIZE):
        frame[y] = (y * 7) % 200
    jpeg = yuv422_planar_to_jpeg(bytes(frame))
    assert jpeg[:2] == b"\xff\xd8"
    with Image.open(BytesIO(jpeg)) as image:
        assert image.size == (320, 240)
        assert image.format == "JPEG"
