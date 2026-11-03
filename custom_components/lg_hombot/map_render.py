"""Render LG HomBot BLK maps using the original web interface colors.

RGBA is used only while compositing overlays and block borders.
The returned image is RGB. Rendering does not import Home Assistant
and does not read or write files.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .blk import CELLS_PER_BLOCK, BlkMap, MapBlock


PIXELS_PER_CELL = 8
CELLS_PER_BLOCK_SIDE = 10

# A real home is far below this (4000 x 4000 pixels, 48 MB as RGB). A corrupt
# BLK file can place a block anywhere, so the size is checked before drawing.
MAX_IMAGE_PIXELS = 16_000_000

BACKGROUND_COLOR = (128, 128, 128)

# Original HomBot web interface colors.
COLOR_FLOOR = (168, 137, 87, 255)
COLOR_WALL = (95, 95, 95, 255)
COLOR_CARPET = (255, 255, 255, 255)

COLOR_SNEAK = (190, 155, 98, 204)
COLOR_SNEAK_WALL = (190, 155, 98, 51)
COLOR_SCREW = (133, 108, 68, 204)

COLOR_FIGHT = (244, 67, 54, 204)
COLOR_BUMP_ABYSS = (118, 94, 107, 204)
COLOR_ABYSS = (0, 0, 223, 204)
COLOR_BUMP = (202, 0, 100, 204)
COLOR_MOVE = (225, 142, 14, 204)

COLOR_BLOCK_DEFAULT = (102, 102, 102, 128)
COLOR_BLOCK_HOME = (102, 0, 0, 128)


def _cell_pixel_position(
    grid_x: int,
    grid_y: int,
    min_x: int,
    min_y: int,
    width_cells: int,
    height_cells: int,
    flip_horizontal: bool,
    flip_vertical: bool,
) -> tuple[int, int]:
    """Convert BLK grid coordinates to output pixel-cell coordinates."""

    relative_x = grid_x - min_x * CELLS_PER_BLOCK_SIDE
    relative_y = grid_y - min_y * CELLS_PER_BLOCK_SIDE

    pixel_x = (
        width_cells - 1 - relative_x
        if flip_horizontal
        else relative_x
    )

    # The original web interface starts with an inverted Y axis.
    pixel_y = (
        relative_y
        if flip_vertical
        else height_cells - 1 - relative_y
    )

    return pixel_x, pixel_y


def _draw_alpha_cell(
    image: Image.Image,
    box: tuple[int, int, int, int],
    color: tuple[int, int, int, int],
) -> None:
    """Draw an alpha-blended color over a rectangular cell."""

    left, top, right, bottom = box

    # The supplied box uses inclusive right/bottom coordinates.
    # PIL crop uses exclusive right/bottom coordinates.
    right += 1
    bottom += 1

    # Clip the rectangle to the image boundaries.
    left = max(0, left)
    top = max(0, top)
    right = min(image.width, right)
    bottom = min(image.height, bottom)

    if left >= right or top >= bottom:
        return

    overlay = Image.new(
        "RGBA",
        (right - left, bottom - top),
        color,
    )

    base = image.crop(
        (left, top, right, bottom)
    ).convert("RGBA")

    result = Image.alpha_composite(
        base,
        overlay,
    ).convert("RGB")

    image.paste(result, (left, top))


def _draw_block_border(
    image: Image.Image,
    block: MapBlock,
    block_index: int,
    min_x: int,
    min_y: int,
    width_cells: int,
    height_cells: int,
    flip_horizontal: bool,
    flip_vertical: bool,
) -> None:
    """Draw one block outline after its cells, as in the web renderer."""

    x_start = block.x * CELLS_PER_BLOCK_SIDE
    y_start = block.y * CELLS_PER_BLOCK_SIDE

    x_end = x_start + CELLS_PER_BLOCK_SIDE - 1
    y_end = y_start + CELLS_PER_BLOCK_SIDE - 1

    corners = [
        _cell_pixel_position(
            x,
            y,
            min_x,
            min_y,
            width_cells,
            height_cells,
            flip_horizontal,
            flip_vertical,
        )
        for x in (x_start, x_end)
        for y in (y_start, y_end)
    ]

    left = min(point[0] for point in corners) * PIXELS_PER_CELL
    top = min(point[1] for point in corners) * PIXELS_PER_CELL

    right = (
        (max(point[0] for point in corners) + 1)
        * PIXELS_PER_CELL - 1
    )
    bottom = (
        (max(point[1] for point in corners) + 1)
        * PIXELS_PER_CELL - 1
    )

    color = (
        COLOR_BLOCK_HOME
        if block_index == 0
        else COLOR_BLOCK_DEFAULT
    )

    # Work on the block's own region only. Converting and compositing the
    # whole canvas for every block made the render time grow with
    # (blocks x image area). Pixels outside the outline stay untouched, and a
    # stroke on the canvas edge is clipped there.
    region_left = max(0, left)
    region_top = max(0, top)
    region_right = min(image.width, right + 1)
    region_bottom = min(image.height, bottom + 1)

    if region_left >= region_right or region_top >= region_bottom:
        return

    border_layer = Image.new(
        "RGBA",
        (region_right - region_left, region_bottom - region_top),
        (0, 0, 0, 0),
    )

    draw = ImageDraw.Draw(border_layer)

    draw.rectangle(
        (
            left - region_left,
            top - region_top,
            right - region_left,
            bottom - region_top,
        ),
        outline=color,
        width=1,
    )

    # Alpha compositing requires both images to use RGBA.
    # The final map remains RGB.
    region = image.crop(
        (region_left, region_top, region_right, region_bottom)
    ).convert("RGBA")
    result = Image.alpha_composite(region, border_layer).convert("RGB")

    image.paste(result, (region_left, region_top))


def render_map(
    map_data: BlkMap,
    *,
    flip_horizontal: bool = False,
    flip_vertical: bool = False,
) -> Image.Image:
    """Render a BLK map with original HomBot colors and map layers."""

    if not map_data.blocks:
        raise ValueError("BLK map contains no used blocks")

    block_x = [block.x for block in map_data.blocks]
    block_y = [block.y for block in map_data.blocks]

    min_x = min(block_x)
    max_x = max(block_x)
    min_y = min(block_y)
    max_y = max(block_y)

    width_cells = (
        max_x - min_x + 1
    ) * CELLS_PER_BLOCK_SIDE

    height_cells = (
        max_y - min_y + 1
    ) * CELLS_PER_BLOCK_SIDE

    if (
        width_cells * PIXELS_PER_CELL * height_cells * PIXELS_PER_CELL
        > MAX_IMAGE_PIXELS
    ):
        raise ValueError("BLK map extent is too large")

    image = Image.new(
        "RGB",
        (
            width_cells * PIXELS_PER_CELL,
            height_cells * PIXELS_PER_CELL,
        ),
        BACKGROUND_COLOR,
    )

    # Draw each block's cells in the original file order. The reader stores
    # exactly CELLS_PER_BLOCK cells per used block, one block after the other.
    for block_index, block in enumerate(map_data.blocks):
        start = block_index * CELLS_PER_BLOCK
        for cell in map_data.cells[start : start + CELLS_PER_BLOCK]:
            grid_x = (
                block.x * CELLS_PER_BLOCK_SIDE
                + cell.cell_index % CELLS_PER_BLOCK_SIDE
            )
            grid_y = (
                block.y * CELLS_PER_BLOCK_SIDE
                + cell.cell_index // CELLS_PER_BLOCK_SIDE
            )

            x, y = _cell_pixel_position(
                grid_x,
                grid_y,
                min_x,
                min_y,
                width_cells,
                height_cells,
                flip_horizontal,
                flip_vertical,
            )

            left = x * PIXELS_PER_CELL
            top = y * PIXELS_PER_CELL
            right = left + PIXELS_PER_CELL - 1
            bottom = top + PIXELS_PER_CELL - 1

            box = (left, top, right, bottom)

            # Base floor layer.
            floor_colors = {
                1: COLOR_WALL,
                2: COLOR_CARPET,
                3: COLOR_FLOOR,
            }

            floor_color = floor_colors.get(cell.floor_type)

            if floor_color is not None:
                ImageDraw.Draw(image).rectangle(
                    box,
                    fill=floor_color[:3],
                )

            # Sneaking layer.
            if cell.sneaking:
                sneak_color = (
                    COLOR_SNEAK_WALL
                    if cell.floor_type == 1
                    else COLOR_SNEAK
                )

                _draw_alpha_cell(
                    image,
                    box,
                    sneak_color,
                )

            # Screwing layer.
            if cell.screwing:
                _draw_alpha_cell(
                    image,
                    box,
                    COLOR_SCREW,
                )

            # Mutually exclusive fight / bump-abyss / abyss+bump logic.
            if cell.abyss and cell.screwing:
                _draw_alpha_cell(
                    image,
                    box,
                    COLOR_FIGHT,
                )

            elif cell.collision and cell.abyss:
                _draw_alpha_cell(
                    image,
                    box,
                    COLOR_BUMP_ABYSS,
                )

            else:
                if cell.abyss:
                    _draw_alpha_cell(
                        image,
                        box,
                        COLOR_ABYSS,
                    )

                if cell.collision:
                    _draw_alpha_cell(
                        image,
                        box,
                        COLOR_BUMP,
                    )

            # Move is always the final cell layer.
            if cell.sneaking and cell.screwing:
                _draw_alpha_cell(
                    image,
                    box,
                    COLOR_MOVE,
                )

        # Block outline is drawn after all cells in this block.
        _draw_block_border(
            image,
            block,
            block_index,
            min_x,
            min_y,
            width_cells,
            height_cells,
            flip_horizontal,
            flip_vertical,
        )

    return image
