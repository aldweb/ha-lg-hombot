"""Planar YUV 4:2:2 (I422) conversion used by the HomBot camera."""

from __future__ import annotations

from io import BytesIO

from PIL import Image

FRAME_WIDTH = 320
FRAME_HEIGHT = 240
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT * 2  # 153600
Y_PLANE_SIZE = FRAME_WIDTH * FRAME_HEIGHT  # 76800
CHROMA_PLANE_SIZE = Y_PLANE_SIZE // 2  # 38400
U_PLANE_OFFSET = Y_PLANE_SIZE
V_PLANE_OFFSET = Y_PLANE_SIZE + CHROMA_PLANE_SIZE


def clamp_uint8(value: float) -> int:
    """Clamp and round like a JavaScript Uint8ClampedArray assignment.

    Matches ECMAScript ToUint8Clamp used when writing ImageData pixels.
    """
    if value != value:  # NaN
        return 0
    if value <= 0:
        return 0
    if value >= 255:
        return 255

    n = int(value)  # floor toward zero for positive values
    frac = value - n
    if frac < 0.5:
        return n
    if frac > 0.5:
        return n + 1
    # Exactly .5: round to even
    return n if n % 2 == 0 else n + 1


def validate_yuv_frame(data: bytes | bytearray | memoryview, *, expected_size: int = FRAME_BYTES) -> None:
    """Raise ValueError when the buffer is not an exact HomBot YUV frame."""
    length = len(data)
    if length != expected_size:
        raise ValueError(
            f"Invalid HomBot YUV frame size: expected {expected_size} bytes, got {length}"
        )


def yuv422_planar_to_rgb(
    yuv_data: bytes | bytearray | memoryview,
    *,
    width: int = FRAME_WIDTH,
    height: int = FRAME_HEIGHT,
) -> bytes:
    """Convert planar YUV 4:2:2 (Y plane, then U, then V) to packed RGB bytes.

    Pixel indexing and coefficients match the HomBot web interface overview.js:
    y + 1.371*(v-128), y - 0.336*(u-128) - 0.698*(v-128), y + 1.732*(u-128).
    """
    expected = width * height * 2
    validate_yuv_frame(yuv_data, expected_size=expected)

    size = width * height
    u_base = size
    v_base = size + size // 2
    src = memoryview(yuv_data)
    out = bytearray(size * 3)

    for p in range(size):
        y = src[p]
        chroma_index = p // 2
        u = src[u_base + chroma_index]
        v = src[v_base + chroma_index]

        r = clamp_uint8(y + 1.371 * (v - 128))
        g = clamp_uint8(y - 0.336 * (u - 128) - 0.698 * (v - 128))
        b = clamp_uint8(y + 1.732 * (u - 128))

        o = p * 3
        out[o] = r
        out[o + 1] = g
        out[o + 2] = b

    return bytes(out)


def yuv422_planar_to_jpeg(
    yuv_data: bytes | bytearray | memoryview,
    *,
    width: int = FRAME_WIDTH,
    height: int = FRAME_HEIGHT,
    quality: int = 85,
) -> bytes:
    """Convert a HomBot YUV frame to a JPEG byte string."""
    rgb = yuv422_planar_to_rgb(yuv_data, width=width, height=height)
    image = Image.frombytes("RGB", (width, height), rgb)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()
