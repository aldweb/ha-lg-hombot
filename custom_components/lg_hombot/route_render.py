"""Draw a HomBot route on a rendered BLK map.

Colors, segment rules and event markers follow ``sites/maps/maprenderer.js``.
The map itself still comes from :func:`lg_hombot.map_render.render_map`,
including its orientation. This module does not import Home Assistant.
"""

from __future__ import annotations

import math

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from .bbl import BblLog
from .blk import BlkMap, count_floor_cells
from .map_render import CELLS_PER_BLOCK_SIDE, PIXELS_PER_CELL, render_map
from .route import (
    format_area,
    RouteStatistics,
    RouteTrace,
    RouteWaypoint,
    first_block_origin,
    pose_to_cell,
    read_route,
    statistics_from_trace,
)


# Frontend alphas are 0.5 and 0.3. 0.5 uses 128, the same integer already
# used for the map's 0.5 layers. 0.3 * 255 is 76.5 and is stored as 77.
_ALPHA_HALF = 128
_ALPHA_DEFAULT = 77

COLOR_PATH_HOMING = (0, 255, 0, _ALPHA_HALF)
COLOR_PATH_TRAPPED = (255, 255, 0, _ALPHA_HALF)
COLOR_PATH_TRAPPED_HOMING = (255, 255, 0, _ALPHA_HALF)
COLOR_PATH_CARPET = (255, 0, 255, _ALPHA_HALF)
COLOR_PATH_DEFAULT = (51, 51, 51, _ALPHA_DEFAULT)

COLOR_EVENT_START = (0, 255, 255, 255)
COLOR_EVENT_BUMP = (255, 0, 0, 255)
COLOR_EVENT_TRAP_START = (255, 255, 0, 255)
COLOR_EVENT_TRAP_END = (255, 255, 127, 255)
COLOR_EVENT_HOME = (0, 255, 0, 255)

_POINTER = 5.0
_SHADOW = (51, 51, 51, 255)
_SHADOW_OFFSET = (2, 2)

# Four facts share one row from 480 px. Two columns start at 240 px.
# Narrower images stack the four facts so the full value can wrap.
FOOTER_OUTER = 4
FOOTER_GAP = 4
FOOTER_PAD = 4
FOOTER_WIDE_WIDTH = 480
FOOTER_MEDIUM_WIDTH = 240
FOOTER_ICON = 14
FOOTER_FONT_SIZE = 14
FOOTER_SUPERSCRIPT_SIZE = 9
FOOTER_BAND = (128, 128, 128)
FOOTER_CARD = (255, 255, 255)
FOOTER_INK = (33, 33, 33)


def render_route(
    map_data: BlkMap,
    log: BblLog,
    *,
    flip_horizontal: bool = False,
    flip_vertical: bool = False,
    time_zone: str | None = None,
) -> Image.Image:
    """Render ``map_data`` and draw the original route on a copy.

    A log without a drawable POS record raises ``ValueError`` and does
    not return a map that could be mistaken for a route. One POS record
    is enough: it is marked and no line is required.

    Route points use the same orientation as :func:`render_map`. An
    integer cell coordinate is drawn at that cell's center.
    """

    trace = read_route(log, time_zone=time_zone)
    statistics = statistics_from_trace(map_data, trace)
    origin_x, origin_y = first_block_origin(map_data)
    base = render_map(
        map_data,
        flip_horizontal=flip_horizontal,
        flip_vertical=flip_vertical,
    )

    block_x = [block.x for block in map_data.blocks]
    block_y = [block.y for block in map_data.blocks]
    min_x = min(block_x)
    min_y = min(block_y)
    width_cells = (max(block_x) - min_x + 1) * CELLS_PER_BLOCK_SIDE
    height_cells = (max(block_y) - min_y + 1) * CELLS_PER_BLOCK_SIDE

    def pixel(waypoint: RouteWaypoint) -> tuple[float, float]:
        return _cell_center_pixel(
            *pose_to_cell(waypoint.x, waypoint.y, origin_x, origin_y),
            min_x=min_x,
            min_y=min_y,
            width_cells=width_cells,
            height_cells=height_cells,
            flip_horizontal=flip_horizontal,
            flip_vertical=flip_vertical,
        )

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    _draw_paths(draw, trace, pixel)
    _draw_markers(draw, trace, pixel)
    routed = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    return _append_footer(routed, statistics)


def render_learned_map(
    map_data: BlkMap,
    *,
    flip_horizontal: bool = False,
    flip_vertical: bool = False,
) -> Image.Image:
    """Render a learned map and the area of its used floor cells.

    The map pixels are the same as :func:`render_map`. The footer is
    added underneath and does not scale the map.
    """

    base = render_map(
        map_data,
        flip_horizontal=flip_horizontal,
        flip_vertical=flip_vertical,
    )
    return _append_area_footer(base, format_area(count_floor_cells(map_data)))


def _draw_paths(draw: ImageDraw.ImageDraw, trace: RouteTrace, pixel) -> None:
    """Stroke each fragment. A single waypoint draws no line."""

    for path in trace.paths:
        if len(path.waypoints) < 2:
            continue
        color = _path_color(path.state)
        points = [pixel(waypoint) for waypoint in path.waypoints]
        for start, end in zip(points, points[1:]):
            draw.line((start, end), fill=color, width=2)


def _draw_markers(draw: ImageDraw.ImageDraw, trace: RouteTrace, pixel) -> None:
    """Draw the event triangles from ``drawEvent``."""

    for waypoint in trace.waypoints:
        color = _marker_color(waypoint)
        if color is None:
            continue
        x_pos, y_pos = pixel(waypoint)
        shape = _triangle(x_pos, y_pos, waypoint.rotation_degrees)
        shadow = [
            (point_x + _SHADOW_OFFSET[0], point_y + _SHADOW_OFFSET[1])
            for point_x, point_y in shape
        ]
        draw.polygon(shadow, fill=_SHADOW)
        draw.polygon(shape, fill=color)


def _path_color(state: str) -> tuple[int, int, int, int]:
    if state == "HOMING":
        return COLOR_PATH_HOMING
    if state == "TRAPPED":
        return COLOR_PATH_TRAPPED
    if state in ("TRAPPED_END", "TRAPPED_HOMING"):
        return COLOR_PATH_TRAPPED_HOMING
    if state == "CARPET":
        return COLOR_PATH_CARPET
    return COLOR_PATH_DEFAULT


def _marker_color(waypoint: RouteWaypoint) -> tuple[int, int, int, int] | None:
    if waypoint.state == "START":
        return COLOR_EVENT_START
    if waypoint.state in ("TRAPPED_END", "END"):
        return COLOR_EVENT_HOME
    if waypoint.record.subtype == "Bumping":
        return COLOR_EVENT_BUMP
    if waypoint.record.subtype == "TrapStateStarts":
        return COLOR_EVENT_TRAP_START
    if waypoint.record.subtype == "TrapStateEnds":
        return COLOR_EVENT_TRAP_END
    return None


def _triangle(
    x_pos: float,
    y_pos: float,
    rotation_degrees: int,
) -> list[tuple[float, float]]:
    """Return the canvas triangle rotated in image coordinates.

    Image Y grows downward, as canvas Y does. Positive degrees rotate
    clockwise, which is what ``CanvasRenderingContext2D.rotate`` does.
    The flip flags move the point but do not mirror this angle; the
    original renderer also leaves the angle unflipped.
    """

    theta = math.radians(rotation_degrees)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    local = (
        (0.0, -_POINTER),
        (_POINTER * 0.9, _POINTER),
        (-_POINTER * 0.9, _POINTER),
    )
    points: list[tuple[float, float]] = []
    for local_x, local_y in local:
        rotated_x = local_x * cos_t - local_y * sin_t
        rotated_y = local_x * sin_t + local_y * cos_t
        points.append((x_pos + rotated_x, y_pos + rotated_y))
    return points


def _cell_center_pixel(
    cell_x: float,
    cell_y: float,
    *,
    min_x: int,
    min_y: int,
    width_cells: int,
    height_cells: int,
    flip_horizontal: bool,
    flip_vertical: bool,
) -> tuple[float, float]:
    """Map an absolute cell coordinate to the center of that cell.

    Integer cell coordinates use the same axis orientation as
    ``map_render._cell_pixel_position``.
    """

    relative_x = cell_x - min_x * CELLS_PER_BLOCK_SIDE
    relative_y = cell_y - min_y * CELLS_PER_BLOCK_SIDE

    if flip_horizontal:
        pixel_x = (width_cells - relative_x - 0.5) * PIXELS_PER_CELL
    else:
        pixel_x = (relative_x + 0.5) * PIXELS_PER_CELL

    # Default orientation puts higher grid-Y toward the top of the image.
    if flip_vertical:
        pixel_y = (relative_y + 0.5) * PIXELS_PER_CELL
    else:
        pixel_y = (height_cells - relative_y - 0.5) * PIXELS_PER_CELL

    return pixel_x, pixel_y


@dataclass(frozen=True)
class FooterCard:
    """One statistic card. Coordinates are relative to the footer."""

    label: str
    box: tuple[int, int, int, int]
    lines: tuple[str, ...]
    line_boxes: tuple[tuple[int, int, int, int], ...]
    icon_at: tuple[int, int]
    beside: bool


@dataclass(frozen=True)
class FooterPlan:
    """The four cards under a map of a given width."""

    width: int
    height: int
    columns: int
    cards: tuple[FooterCard, ...]


def footer_columns(width: int) -> int:
    """Return 4, 2 or 1 columns for this image width."""

    if width >= FOOTER_WIDE_WIDTH:
        return 4
    if width >= FOOTER_MEDIUM_WIDTH:
        return 2
    return 1


def footer_plan(width: int, labels: tuple[str, str, str, str]) -> FooterPlan:
    """Lay the four values out without clipping or ellipsis.

    A value stays on the icon's row when it fits. Otherwise it continues
    on the next lines and breaks only at spaces.
    """

    font = _footer_font()
    columns = footer_columns(width)
    card_width = (
        width - 2 * FOOTER_OUTER - (columns - 1) * FOOTER_GAP
    ) // columns
    beside_width = card_width - 2 * FOOTER_PAD - FOOTER_ICON - FOOTER_GAP
    stack_width = card_width - 2 * FOOTER_PAD
    line_height = _text_origin_height(font)
    drafted: list[tuple[str, tuple[str, ...], bool, int]] = []
    for label in labels:
        beside = _text_width(font, label) <= beside_width
        lines = (label,) if beside else _wrap_value(font, label, stack_width)
        content_height = (
            FOOTER_PAD + max(FOOTER_ICON, line_height) + FOOTER_PAD
            if beside
            else (
                FOOTER_PAD
                + FOOTER_ICON
                + FOOTER_GAP
                + line_height * len(lines)
                + FOOTER_GAP * (len(lines) - 1)
                + FOOTER_PAD
            )
        )
        drafted.append((label, lines, beside, content_height))

    cards: list[FooterCard] = []
    top = FOOTER_OUTER
    for row_index in range((len(drafted) + columns - 1) // columns):
        row = drafted[row_index * columns:(row_index + 1) * columns]
        row_height = max(item[3] for item in row)
        for column, (label, lines, beside, _content_height) in enumerate(row):
            left = FOOTER_OUTER + column * (card_width + FOOTER_GAP)
            icon_at = (left + FOOTER_PAD, top + FOOTER_PAD)
            if beside:
                text_x = left + FOOTER_PAD + FOOTER_ICON + FOOTER_GAP
                text_y = top + FOOTER_PAD
                line_boxes = ((
                    text_x,
                    text_y,
                    _text_width(font, lines[0]),
                    line_height,
                ),)
            else:
                text_x = left + FOOTER_PAD
                text_y = top + FOOTER_PAD + FOOTER_ICON + FOOTER_GAP
                line_boxes = tuple(
                    (
                        text_x,
                        text_y + index * (line_height + FOOTER_GAP),
                        _text_width(font, line),
                        line_height,
                    )
                    for index, line in enumerate(lines)
                )
            cards.append(FooterCard(
                label=label,
                box=(left, top, left + card_width, top + row_height),
                lines=lines,
                line_boxes=line_boxes,
                icon_at=icon_at,
                beside=beside,
            ))
        top += row_height + FOOTER_GAP
    return FooterPlan(
        width=width,
        height=top - FOOTER_GAP + FOOTER_OUTER,
        columns=columns,
        cards=tuple(cards),
    )


def footer_height(width: int, labels: tuple[str, str, str, str]) -> int:
    """Return the footer height for these four values at this width."""

    return footer_plan(width, labels).height


def footer_card_boxes(
    width: int,
    labels: tuple[str, str, str, str],
) -> tuple[tuple[int, int, int, int], ...]:
    """Return the four card boxes in footer coordinates."""

    return tuple(card.box for card in footer_plan(width, labels).cards)


def _append_area_footer(map_image: Image.Image, label: str) -> Image.Image:
    """Place one area card under the map. The map pixels are not scaled."""

    width, height = map_image.size
    font = _footer_font()
    line_height = _text_origin_height(font)
    card_width = width - 2 * FOOTER_OUTER
    beside_width = card_width - 2 * FOOTER_PAD - FOOTER_ICON - FOOTER_GAP
    beside = _text_width(font, label) <= beside_width
    content_height = (
        max(FOOTER_ICON, line_height)
        if beside
        else FOOTER_ICON + FOOTER_GAP + line_height
    )
    card_height = FOOTER_PAD + content_height + FOOTER_PAD
    image = Image.new(
        "RGB",
        (width, height + FOOTER_OUTER + card_height + FOOTER_OUTER),
        FOOTER_BAND,
    )
    image.paste(map_image, (0, 0))
    left = FOOTER_OUTER
    top = height + FOOTER_OUTER
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (left, top, left + card_width - 1, top + card_height - 1),
        radius=2,
        fill=FOOTER_CARD,
    )
    _icon_area(draw, left + FOOTER_PAD, top + FOOTER_PAD)
    if beside:
        text_x = left + FOOTER_PAD + FOOTER_ICON + FOOTER_GAP
        text_y = top + FOOTER_PAD
    else:
        text_x = left + FOOTER_PAD
        text_y = top + FOOTER_PAD + FOOTER_ICON + FOOTER_GAP
    _draw_footer_text(draw, text_x, text_y, label)
    return image


def _append_footer(route_image: Image.Image, statistics: RouteStatistics) -> Image.Image:
    """Place the four facts under the map. The map pixels are not scaled."""

    width, height = route_image.size
    labels = (
        statistics.area_text,
        statistics.duration_text,
        statistics.distance_text,
        statistics.bumps_text,
    )
    plan = footer_plan(width, labels)
    image = Image.new("RGB", (width, height + plan.height), FOOTER_BAND)
    image.paste(route_image, (0, 0))
    draw = ImageDraw.Draw(image)
    icons = (_icon_area, _icon_duration, _icon_distance, _icon_bumps)
    for card, icon in zip(plan.cards, icons):
        left, top, right, bottom = card.box
        draw.rounded_rectangle(
            (left, height + top, right - 1, height + bottom - 1),
            radius=2,
            fill=FOOTER_CARD,
        )
        icon(draw, card.icon_at[0], height + card.icon_at[1])
        for line, (text_x, text_y, _text_w, _text_h) in zip(card.lines, card.line_boxes):
            if line:
                _draw_footer_text(draw, text_x, height + text_y, line)
    return image


def _wrap_value(font: ImageFont.ImageFont, text: str, max_width: int) -> tuple[str, ...]:
    """Wrap on spaces. A value without spaces stays one line."""

    if " " not in text or _text_width(font, text) <= max_width:
        return (text,)
    words = text.split(" ")
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        proposal = f"{current} {word}"
        if _text_width(font, proposal) <= max_width:
            current = proposal
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return tuple(lines)


def footer_text_pieces(
    text: str,
    font: ImageFont.ImageFont | None = None,
) -> tuple[tuple[str, int, int, bool], ...]:
    """Split one footer value into positioned pieces.

    Pillow's bundled Aileron font has no ``²`` glyph and draws a missing
    mark instead. The superscript is therefore a raised ``2`` from that
    same font. Each piece is ``(text, x, y, superscript)`` relative to
    the line origin.
    """

    base = _footer_font() if font is None else font
    small = _superscript_font()
    y_offset = base.getbbox("0")[1] - small.getbbox("2")[1]
    pieces: list[tuple[str, int, int, bool]] = []
    x_pos = 0
    for chunk, raised in _split_superscript(text):
        face = small if raised else base
        pieces.append((chunk, x_pos, y_offset if raised else 0, raised))
        x_pos += _raw_width(face, chunk)
    return tuple(pieces)


def _split_superscript(text: str) -> tuple[tuple[str, bool], ...]:
    parts: list[tuple[str, bool]] = []
    plain: list[str] = []
    for character in text:
        if character == "²":
            if plain:
                parts.append(("".join(plain), False))
                plain = []
            parts.append(("2", True))
        else:
            plain.append(character)
    if plain:
        parts.append(("".join(plain), False))
    return tuple(parts)


def _draw_footer_text(
    draw: ImageDraw.ImageDraw,
    x_pos: int,
    y_pos: int,
    text: str,
) -> None:
    base = _footer_font()
    small = _superscript_font()
    for chunk, dx_pos, dy_pos, raised in footer_text_pieces(text, base):
        draw.text(
            (x_pos + dx_pos, y_pos + dy_pos),
            chunk,
            fill=FOOTER_INK,
            font=small if raised else base,
        )


def _text_width(font: ImageFont.ImageFont, text: str) -> int:
    pieces = footer_text_pieces(text, font)
    if not pieces:
        return 0
    chunk, dx_pos, _dy_pos, raised = pieces[-1]
    face = _superscript_font() if raised else font
    return dx_pos + _raw_width(face, chunk)


def _raw_width(font: ImageFont.ImageFont, text: str) -> int:
    if not text:
        return 0
    box = font.getbbox(text)
    return box[2] - box[0]


def _text_origin_height(font: ImageFont.ImageFont) -> int:
    """Return the distance from a text origin to the bottom of the ink."""

    return font.getbbox("Ay")[3]


def _footer_font() -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(FOOTER_FONT_SIZE)
    except TypeError:
        return ImageFont.load_default()


def _superscript_font() -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(FOOTER_SUPERSCRIPT_SIZE)
    except TypeError:
        return _footer_font()


def _icon_area(draw: ImageDraw.ImageDraw, left: int, top: int) -> None:
    draw.rectangle((left, top, left + 14, top + 14), outline=FOOTER_INK)
    draw.rectangle((left + 3, top + 3, left + 11, top + 11), outline=FOOTER_INK)


def _icon_duration(draw: ImageDraw.ImageDraw, left: int, top: int) -> None:
    draw.ellipse((left, top, left + 14, top + 14), outline=FOOTER_INK)
    draw.line((left + 7, top + 7, left + 7, top + 3), fill=FOOTER_INK)
    draw.line((left + 7, top + 7, left + 11, top + 9), fill=FOOTER_INK)


def _icon_distance(draw: ImageDraw.ImageDraw, left: int, top: int) -> None:
    draw.line((left, top + 10, left + 14, top + 10), fill=FOOTER_INK)
    for tick in range(5):
        x_pos = left + tick * 3
        draw.line((x_pos, top + 7, x_pos, top + 13), fill=FOOTER_INK)


def _icon_bumps(draw: ImageDraw.ImageDraw, left: int, top: int) -> None:
    """Draw a bent arrow in the 14 by 14 footer box."""

    draw.line((left + 2, top + 11, left + 6, top + 11), fill=FOOTER_INK, width=2)
    draw.line((left + 6, top + 11, left + 11, top + 4), fill=FOOTER_INK, width=2)
    draw.line((left + 7, top + 4, left + 11, top + 4), fill=FOOTER_INK, width=2)
    draw.line((left + 11, top + 4, left + 11, top + 8), fill=FOOTER_INK, width=2)
