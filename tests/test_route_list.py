"""Synthetic tests for the HomBot cleaning-run list parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lg_hombot.route_list import parse_route_list


BLK_101 = "MAPDATA20010101010121_123456_101.blk"
BBL_101 = "cleanlog20010101010101_U_101.bbl"
BLK_102 = "MAPDATA20010202020222_234567_102.blk"
BBL_102 = "cleanlog20010202020202_U_102.bbl"


def _document(
    maps: list[object] | object | None = None,
    logs: list[object] | object | None = None,
    *,
    blackbox: object = "/.../usr/data/blackbox/",
    global_map: object = "/.../usr/data/MapReuseNavi",
    omit: set[str] | None = None,
) -> str:
    document: dict[str, object] = {
        "blackbox": blackbox,
        "global_map": global_map,
        "maps": [BLK_101] if maps is None else maps,
        "logs": [BBL_101] if logs is None else logs,
    }
    for key in omit or ():
        document.pop(key, None)
    return json.dumps(document)


def test_parser_does_not_import_home_assistant_or_aiohttp() -> None:
    source = Path(parse_route_list.__globals__["__file__"]).read_text(encoding="utf-8")
    assert "homeassistant" not in source
    assert "aiohttp" not in source
    assert "last match" in source


def test_valid_json_bytes_and_text_return_one_run() -> None:
    text = _document()

    from_text = parse_route_list(text)
    from_bytes = parse_route_list(text.encode("utf-8"))

    assert from_bytes == from_text
    assert len(from_text.runs) == 1
    run = from_text.runs[0]
    assert run.bbl_name == BBL_101
    assert run.blk_name == BLK_101
    assert run.run_id == "101"
    assert run.label == "01.01.2001 - 01:01"


def test_shorter_maps_array_and_log_not_paired_by_index() -> None:
    text = _document(
        maps=[BLK_102, BLK_101],
        logs=[BBL_101, "cleanlog20010202020202_U_109.bbl", BBL_102],
    )

    runs = parse_route_list(text).runs

    assert [(run.blk_name, run.bbl_name) for run in runs] == [
        (BLK_102, BBL_102),
        (BLK_101, BBL_101),
    ]


def test_missing_fields_are_rejected() -> None:
    for key in ("blackbox", "global_map", "maps", "logs"):
        with pytest.raises(ValueError, match="route list is invalid"):
            parse_route_list(_document(omit={key}))


def test_invalid_json_is_rejected() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_route_list("{")
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_route_list(b"\xff")


def test_literal_placeholders_are_skipped() -> None:
    text = _document(
        maps=["${BLKFILE:0}", BLK_101, "${BLKFILE:2}"],
        logs=["${BBLFILE:0}", BBL_101],
    )

    runs = parse_route_list(text).runs

    assert [run.blk_name for run in runs] == [BLK_101]


def test_unsafe_file_names_are_skipped() -> None:
    text = _document(
        maps=[
            "../MAPDATA20010101010121_123456_101.blk",
            "/usr/data/blackbox/" + BLK_101,
            "folder\\" + BLK_101,
            "",
            " ",
            BLK_102,
        ],
        logs=[
            "../" + BBL_102,
            "folder/" + BBL_102,
            BBL_102,
        ],
    )

    runs = parse_route_list(text).runs

    assert [run.bbl_name for run in runs] == [BBL_102]
    assert runs[0].blk_name == BLK_102


def test_invalid_mapdata_names_are_skipped() -> None:
    text = _document(
        maps=["not-a-map.blk", "MAPDATA_short.blk", BLK_101],
        logs=[BBL_101],
    )

    runs = parse_route_list(text).runs

    assert [run.blk_name for run in runs] == [BLK_101]


def test_run_id_does_not_match_a_longer_numeric_suffix() -> None:
    text = _document(
        maps=[BLK_101],
        logs=["cleanlog20010101010101_U_1010.bbl", BBL_101],
    )

    runs = parse_route_list(text).runs

    assert len(runs) == 1
    assert runs[0].bbl_name == BBL_101

    only_longer = _document(logs=["cleanlog20010101010101_U_1010.bbl"])
    assert parse_route_list(only_longer).runs == ()


def test_last_matching_log_wins() -> None:
    first = "aaa_U_101.bbl"
    second = "zzz_U_101.bbl"
    text = _document(logs=[first, "cleanlog_U_1010.bbl", second])

    runs = parse_route_list(text).runs

    assert len(runs) == 1
    assert runs[0].bbl_name == second


def test_map_without_a_log_is_omitted() -> None:
    text = _document(maps=[BLK_101, BLK_102], logs=[BBL_102])

    runs = parse_route_list(text).runs

    assert [run.run_id for run in runs] == ["102"]


def test_duplicate_labels_include_the_run_id() -> None:
    same_minute = "MAPDATA20010101010121_000001_103.blk"
    text = _document(
        maps=[BLK_101, same_minute],
        logs=[BBL_101, "cleanlog20010101010121_U_103.bbl"],
    )

    labels = [run.label for run in parse_route_list(text).runs]

    assert labels == [
        "01.01.2001 - 01:01 (101)",
        "01.01.2001 - 01:01 (103)",
    ]


def test_only_the_first_ten_map_entries_are_used() -> None:
    # Seconds differ, so every name is a valid MAPDATA file. The minute,
    # and therefore the date label, stays the same.
    maps = [
        f"MAPDATA200101010101{index:02d}_000001_{index}.blk" for index in range(11)
    ]
    logs = [f"cleanlog20010101010121_U_{index}.bbl" for index in range(11)]
    text = _document(maps=maps, logs=logs)

    runs = parse_route_list(text).runs

    assert len(runs) == 10
    assert runs[-1].run_id == "9"
    assert all(run.run_id != "10" for run in runs)


def test_empty_arrays_return_no_runs() -> None:
    parsed = parse_route_list(_document(maps=[], logs=[]))

    assert parsed.runs == ()


def test_unknown_blackbox_path_is_rejected() -> None:
    text = _document(blackbox="/tmp/blackbox/")

    with pytest.raises(ValueError, match="blackbox path is invalid"):
        parse_route_list(text)


def test_filesystem_blackbox_prefix_is_accepted() -> None:
    text = _document(blackbox="/usr/data/blackbox/")

    assert parse_route_list(text).runs[0].run_id == "101"
