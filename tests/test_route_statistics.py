"""Synthetic tests for route statistics and the footer under the map."""

from __future__ import annotations

import pytest

from lg_hombot.bbl import read_bbl_bytes
from lg_hombot.blk import BlkHeader, BlkMap, MapBlock, MapCell
from lg_hombot.map_render import render_map
from lg_hombot.route import format_area, format_distance, format_duration, route_statistics
from lg_hombot.route_render import (
    FOOTER_CARD,
    FOOTER_INK,
    FOOTER_SUPERSCRIPT_SIZE,
    footer_columns,
    footer_height,
    footer_plan,
    footer_text_pieces,
    render_route,
)


def _log(body: str) -> object:
    return read_bbl_bytes(f"example\n{body}".encode("ascii"))


def _map(positions: list[tuple[int, int]], floors: dict[int, dict[int, int]]) -> BlkMap:
    blocks = [
        MapBlock(0, 0, y_pos * 100 + x_pos, 0)
        for x_pos, y_pos in positions
    ]
    cells = []
    for block_index in range(len(blocks)):
        for cell_index in range(100):
            value = floors.get(block_index, {}).get(cell_index, 0)
            cells.append(MapCell(block_index, cell_index, value))
    return BlkMap(
        header=BlkHeader(1, 0, 0, 0, 0, 100, len(blocks), 0, len(blocks), 10, 100),
        blocks=tuple(blocks),
        cells=tuple(cells),
        file_size=0,
        trailer_size=0,
    )


def _pose(second: int, subtype: str, x_pos: int, y_pos: int, info: str = "") -> str:
    stamp = f"1999010101{second // 60:02d}{second % 60:02d}.000001"
    return f"{stamp},POSI,{subtype},{x_pos},{y_pos},0,{info}\n"


def test_area_counts_only_normal_and_carpet_cells() -> None:
    floors = {
        0: {0: 3, 1: 2, 2: 1, 3: 0},
        1: {4: 3, 5: 3, 6: 2},
    }
    log = _log(_pose(0, "RobotPose", 0, 0))
    stats = route_statistics(_map([(10, 10), (11, 10)], floors), log)

    assert stats.floor_cells == 5
    assert stats.area_m2 == 0.05
    assert stats.area_text == "0.05m²"
    assert format_area(0) == "0m²"
    assert format_area(100) == "1m²"
    assert format_area(150) == "1.5m²"
    assert format_area(1000) == "10m²"


def test_duration_uses_only_pos_clocks_and_the_original_parts() -> None:
    body = (
        "19990101020000.000001,STRN,App_SB,Begin (Docked )\n"
        + _pose(10, "RobotPose", 0, 0)
        + "19990101025959.999999,STRN,App_SB,End Cleaning\n"
        + _pose(2375, "RobotPose", 100, 0)
    )
    stats = route_statistics(_map([(10, 10)], {0: {0: 3}}), _log(body))

    assert stats.duration_seconds == 2375 - 10
    assert stats.duration_text == "39min 25s"
    assert format_duration(0) == "1s"
    assert format_duration(7384) == "2h 3min 4s"
    assert format_duration(3600) == "1h"
    assert format_duration(None) == ""


def test_one_pos_record_is_shown_as_one_second() -> None:
    stats = route_statistics(
        _map([(10, 10)], {0: {0: 3}}),
        _log(_pose(5, "RobotPose", 0, 0)),
    )

    assert stats.duration_seconds == 0
    assert stats.duration_text == "1s"


def test_unreadable_timestamp_leaves_the_duration_empty() -> None:
    log = _log(
        "not-a-clock,POSI,RobotPose,0,0,0,\n"
        + _pose(5, "RobotPose", 10, 0)
    )

    stats = route_statistics(_map([(10, 10)], {}), log)

    assert stats.duration_seconds is None
    assert stats.duration_text == ""


def test_invalid_clock_fields_leave_the_duration_empty() -> None:
    log = _log("19991301010101.000001,POSI,RobotPose,0,0,0,\n")

    stats = route_statistics(_map([(10, 10)], {}), log)

    assert stats.duration_text == ""


def test_distance_between_poses_and_the_strn_origin_anchor() -> None:
    from_origin = _log(
        "19990101010101.000001,STRN,App_SB,Begin (Docked )\n"
        + _pose(1, "RobotPose", 3000, 4000)
    )
    chained = _log(
        _pose(1, "RobotPose", 1000, 0)
        + _pose(2, "RobotPose", 4000, 0)
    )
    several = _log(
        _pose(1, "RobotPose", 0, 0)
        + _pose(2, "RobotPose", 3000, 0)
        + _pose(3, "RobotPose", 3000, 4000)
    )
    still = _log(_pose(1, "RobotPose", 0, 0))
    missing_anchor = _log(_pose(1, "RobotPose", 3000, 4000))
    invalid = _log(
        "19990101010101.000001,POSI,RobotPose,abc,def,ghi\n"
        + _pose(2, "RobotPose", 1000, 0)
    )
    blank = _map([(10, 10)], {})

    assert route_statistics(blank, from_origin).distance_m == 5
    assert route_statistics(blank, from_origin).distance_text == "5m"
    assert route_statistics(blank, chained).distance_m == 4
    assert route_statistics(blank, several).distance_m == 7
    assert route_statistics(blank, still).distance_m == 0
    assert route_statistics(blank, still).distance_text == "0m"
    assert route_statistics(blank, missing_anchor).distance_m == 5
    assert route_statistics(blank, invalid).distance_m == 1
    assert format_distance(1.25) == "1.25m"
    assert format_distance(1.2) == "1.2m"


def test_bumps_count_only_pos_bumping_records() -> None:
    log = _log(
        _pose(1, "Bumping", 0, 0, "gyro")
        + _pose(2, "RobotPose", 10, 0)
        + _pose(3, "Bumping", 20, 0, "acc")
        + "19990101010104.000001,EVNT,Bumping,1,2,3\n"
    )
    none = _log(_pose(1, "RobotPose", 0, 0))
    blank = _map([(10, 10)], {})

    assert route_statistics(blank, log).bumps == 2
    assert route_statistics(blank, log).bumps_text == "2"
    assert route_statistics(blank, none).bumps == 0
    assert route_statistics(blank, none).bumps_text == "0"


def test_a_log_without_pos_records_still_raises() -> None:
    log = _log("19990101010101.000001,STRN,App_SB,Begin (Docked )\n")

    with pytest.raises(ValueError, match="no POS records"):
        route_statistics(_map([(10, 10)], {0: {0: 3}}), log)


def test_amsterdam_daylight_saving_matches_local_unix_time() -> None:
    blank = _map([(10, 10)], {})
    spring = _log(
        "20260329013000.000001,POSI,RobotPose,0,0,0,\n"
        "20260329033000.000001,POSI,RobotPose,10,0,0,\n"
    )
    autumn = _log(
        "20261025013000.000001,POSI,RobotPose,0,0,0,\n"
        "20261025033000.000001,POSI,RobotPose,10,0,0,\n"
    )
    repeated = _log(
        "20261025023000.000001,POSI,RobotPose,0,0,0,\n"
        "20261025033000.000001,POSI,RobotPose,10,0,0,\n"
    )
    zone = "Europe/Amsterdam"

    spring_local = route_statistics(blank, spring, time_zone=zone)
    autumn_local = route_statistics(blank, autumn, time_zone=zone)
    repeated_local = route_statistics(blank, repeated, time_zone=zone)

    assert spring_local.duration_seconds == 3600
    assert spring_local.duration_text == "1h"
    assert autumn_local.duration_seconds == 10800
    assert autumn_local.duration_text == "3h"
    # 02:30 with fold=0 is the first, still-daylight occurrence, so the
    # step to 03:30 standard time is two hours rather than one.
    assert repeated_local.duration_seconds == 7200
    assert repeated_local.duration_text == "2h"
    assert route_statistics(blank, spring).duration_text == "2h"
    assert route_statistics(blank, autumn).duration_text == "2h"
    assert route_statistics(blank, repeated).duration_text == "1h"
    assert spring_local.distance_m == route_statistics(blank, spring).distance_m


def test_unreadable_clock_stays_empty_with_a_timezone() -> None:
    log = _log("20261301010101.000001,POSI,RobotPose,0,0,0,\n")
    stats = route_statistics(
        _map([(10, 10)], {}),
        log,
        time_zone="Europe/Amsterdam",
    )

    assert stats.duration_seconds is None
    assert stats.duration_text == ""


def test_square_metre_superscript_is_a_visible_two() -> None:
    from PIL import Image, ImageDraw, ImageFont

    floors = {index: {cell: 3 for cell in range(100)} for index in range(30)}
    floors[30] = {cell: 3 for cell in range(40)}
    map_data = _map([(40, 40 + index) for index in range(31)], floors)
    log = _log(
        "19990101000000.000001,POSI,RobotPose,0,0,0,\n"
    )
    plain = render_map(map_data)
    image = render_route(map_data, log)
    stats = route_statistics(map_data, log)
    plan = footer_plan(plain.size[0], (
        stats.area_text,
        stats.duration_text,
        stats.distance_text,
        stats.bumps_text,
    ))

    assert plain.size == (80, 80 * 31)
    assert image.size[0] == plain.size[0] == 80
    assert stats.area_text == "30.4m²"
    raised = [piece for piece in footer_text_pieces("30.4m²") if piece[3]]
    assert len(raised) == 1
    glyph, dx_pos, dy_pos, _superscript = raised[0]
    assert glyph == "2"
    assert dy_pos != 0
    text_x, text_y, _width, _height = plan.cards[0].line_boxes[0]
    small = ImageFont.load_default(FOOTER_SUPERSCRIPT_SIZE)
    box = small.getbbox("2")
    reference = Image.new("RGB", (box[2], box[3]), FOOTER_CARD)
    ImageDraw.Draw(reference).text((0, 0), "2", font=small, fill=FOOTER_INK)
    origin_x = text_x + dx_pos
    origin_y = text_y + dy_pos
    footer = image.crop((0, plain.size[1], image.size[0], image.size[1]))
    for y_pos in range(reference.size[1]):
        for x_pos in range(reference.size[0]):
            assert footer.getpixel((origin_x + x_pos, origin_y + y_pos)) == (
                reference.getpixel((x_pos, y_pos))
            )


def test_single_block_route_stays_eighty_pixels_wide() -> None:
    map_data = _map([(10, 10)], {0: {index: 3 for index in range(100)}})
    plain = render_map(map_data)
    routed = render_route(map_data, _log(_pose(1, "RobotPose", 0, 0)))

    assert plain.size == (80, 80)
    assert routed.size[0] == plain.size[0]


def test_footer_shows_four_values_below_an_unflipped_map() -> None:
    floors = {0: {index: 3 for index in range(50)}}
    map_data = _map([(10, 10)], floors)
    log = _log(
        _pose(0, "RobotPose", 0, 0)
        + _pose(65, "Bumping", 1000, 0, "gyro")
    )
    plain = render_map(map_data)
    routed = render_route(map_data, log)
    flipped = render_route(map_data, log, flip_horizontal=True)
    stats = route_statistics(map_data, log)
    width, height = plain.size

    assert (stats.area_text, stats.duration_text, stats.distance_text, stats.bumps_text) == (
        "0.5m²",
        "1min 5s",
        "1m",
        "1",
    )
    labels = (
        stats.area_text,
        stats.duration_text,
        stats.distance_text,
        stats.bumps_text,
    )
    assert routed.size == (width, height + footer_height(width, labels))
    assert routed.crop((0, 0, width, height)).size == plain.size
    footer = routed.crop((0, height, width, routed.size[1]))
    flipped_footer = flipped.crop((0, height, width, flipped.size[1]))
    assert footer.tobytes() == flipped_footer.tobytes()
    assert routed.crop((0, 0, width, height)).tobytes() != (
        flipped.crop((0, 0, width, height)).tobytes()
    )
    assert footer.getpixel((6, 6)) == FOOTER_CARD
    assert routed.getpixel((4, 4)) == plain.getpixel((4, 4))


def test_footer_breakpoints_keep_every_character() -> None:
    labels = ("30.4m²", "12h 39min 35s", "186.76m", "52")
    short = ("0.5m²", "39min 35s", "12.5m", "52")
    expected = {80: (1, 4), 160: (1, 4), 239: (1, 4), 240: (2, 2), 479: (2, 2), 480: (4, 1)}

    for width, (columns, rows) in expected.items():
        for values in (labels, short):
            plan = footer_plan(width, values)
            tops = {card.box[1] for card in plan.cards}
            assert footer_columns(width) == columns
            assert plan.columns == columns
            assert len(tops) == rows
            assert footer_height(width, values) == plan.height
            for card in plan.cards:
                assert " ".join(card.lines) == card.label
                left, top, right, bottom = card.box
                assert card.icon_at[0] >= left
                assert card.icon_at[1] >= top
                for line in card.lines:
                    assert " " not in line or line in card.label
                for text_x, text_y, text_width, text_height in card.line_boxes:
                    assert text_x >= left + 4
                    assert text_y >= top + 4
                    assert text_x + text_width <= right - 4
                    assert text_y + text_height <= bottom


def test_long_duration_wraps_only_on_spaces_when_the_card_is_narrow() -> None:
    plan = footer_plan(80, ("30.4m²", "12h 39min 35s", "186.76m", "52"))
    duration = plan.cards[1]

    assert duration.beside is False
    assert duration.lines == ("12h", "39min 35s")
    assert all(" " not in line or line == "39min 35s" for line in duration.lines)


def test_rendered_footer_ink_stays_inside_the_cards_for_every_flip() -> None:
    labels_log = (
        "19990101000000.000001,STRN,App_SB,Begin (Docked )\n"
        "19990101000000.000001,POSI,RobotPose,0,0,0,\n"
        + "".join(
            "19990101010000.000001,POSI,Bumping,186760,0,0,\n"
            for _index in range(51)
        )
        + "19990101123935.000001,POSI,Bumping,186760,0,0,\n"
    )
    floors = {index: {cell: 3 for cell in range(100)} for index in range(30)}
    floors[30] = {cell: 3 for cell in range(40)}
    widths = {
        80: [(40, 40 + index) for index in range(31)],
        160: [(40 + index % 2, 40 + index // 2) for index in range(31)],
        240: [(40 + index % 3, 40 + index // 3) for index in range(31)],
        480: [(40 + index % 6, 40 + index // 6) for index in range(31)],
    }

    for width, positions in widths.items():
        map_data = _map(positions, floors)
        log = _log(labels_log)
        stats = route_statistics(map_data, log)
        assert (
            stats.area_text,
            stats.duration_text,
            stats.distance_text,
            stats.bumps_text,
        ) == ("30.4m²", "12h 39min 35s", "186.76m", "52")
        plain = render_map(map_data)
        images = [
            render_route(map_data, log),
            render_route(map_data, log, flip_horizontal=True),
            render_route(map_data, log, flip_vertical=True),
            render_route(
                map_data,
                log,
                flip_horizontal=True,
                flip_vertical=True,
            ),
        ]
        map_height = plain.size[1]
        footers = [
            image.crop((0, map_height, width, image.size[1]))
            for image in images
        ]
        assert plain.size[0] == width
        assert all(image.size == (width, map_height + footers[0].size[1]) for image in images)
        assert all(footer.tobytes() == footers[0].tobytes() for footer in footers)
        assert images[0].crop((0, 0, width, map_height)).tobytes() != (
            images[1].crop((0, 0, width, map_height)).tobytes()
        )
        plan = footer_plan(width, (
            stats.area_text,
            stats.duration_text,
            stats.distance_text,
            stats.bumps_text,
        ))
        footer = footers[0]
        for card in plan.cards:
            for line, (text_x, text_y, text_width, text_height) in zip(
                card.lines, card.line_boxes
            ):
                if not line:
                    continue
                assert any(
                    footer.getpixel((x_pos, y_pos)) == FOOTER_INK
                    for y_pos in range(text_y, text_y + text_height)
                    for x_pos in range(text_x, text_x + text_width)
                )
        for y_pos in range(footer.size[1]):
            for x_pos in range(footer.size[0]):
                if footer.getpixel((x_pos, y_pos)) != FOOTER_INK:
                    continue
                assert any(
                    card.box[0] <= x_pos < card.box[2]
                    and card.box[1] <= y_pos < card.box[3]
                    for card in plan.cards
                )
