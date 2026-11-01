"""Synthetic tests for the HomBot BBL reader."""

from __future__ import annotations

from pathlib import Path

import pytest

from lg_hombot.bbl import read_bbl, read_bbl_bytes


MAPDATA = "/usr/data/blackbox/MAPDATA20010101010121_123456_101.blk"


def _write_log(path: Path, text: str) -> None:
    path.write_text(text, encoding="ascii")


def test_parser_does_not_import_home_assistant() -> None:
    source = Path(read_bbl.__globals__["__file__"]).read_text(encoding="utf-8")
    assert "homeassistant" not in source


def test_log_id_and_robot_pose(tmp_path: Path) -> None:
    path = tmp_path / "pose.bbl"
    raw_pose = "20010101010101.000001,POSI,RobotPose,1180,53,-30"
    _write_log(
        path,
        "20010101010101_U_101\n"
        f"{raw_pose}\n",
    )

    log = read_bbl(path)
    pose = log.robot_poses[0]

    assert log.log_id == "20010101010101_U_101"
    assert log.source == path
    assert len(log.robot_poses) == 1
    assert pose.timestamp == "20010101010101.000001"
    assert pose.record_type == "POSI"
    assert pose.subtype == "RobotPose"
    assert pose.values == ("1180", "53", "-30")
    assert pose.raw_line == raw_pose
    assert pose.line_number == 2
    assert pose.x == 1180
    assert pose.y == 53
    assert pose.heading == -30
    assert pose.is_position_record is True


def test_bumping_and_event_pose(tmp_path: Path) -> None:
    path = tmp_path / "events.bbl"
    _write_log(
        path,
        "log-events\n"
        "20010101010102.000001,EVNT,Bumping,gyro\n"
        "20010101010103.000001,EVNT,EventPose,12,34\n",
    )

    log = read_bbl(path)
    bumping = log.records_by_subtype("Bumping")
    event_pose = log.records_by_subtype("EventPose")

    assert len(bumping) == 1
    assert bumping[0].record_type == "EVNT"
    assert bumping[0].values == ("gyro",)
    assert bumping[0].x is None
    assert bumping[0].is_position_record is False
    assert len(event_pose) == 1
    assert event_pose[0].values == ("12", "34")
    assert event_pose[0].line_number == 3


def test_trap_state_records(tmp_path: Path) -> None:
    path = tmp_path / "traps.bbl"
    _write_log(
        path,
        "log-traps\n"
        "20010101010104.000001,STAT,TrapStateStarts,SB cleaning\n"
        "20010101010105.000001,STAT,TrapStateEnds,end\n",
    )

    log = read_bbl(path)

    assert log.records_by_subtype("TrapStateStarts")[0].values == ("SB cleaning",)
    assert log.records_by_subtype("TrapStateEnds")[0].values == ("end",)
    assert log.records_by_type("STAT")[1].subtype == "TrapStateEnds"


def test_mapdata_reference_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "with-map.bbl"
    raw_map = f"20010101010121.000001,BULK,MAPDATA,{MAPDATA}"
    _write_log(
        path,
        "log-map\n"
        f"{raw_map}\n",
    )

    log = read_bbl(path)
    reference = log.records_by_subtype("MAPDATA")[0]

    assert log.map_references == (MAPDATA,)
    assert reference.record_type == "BULK"
    assert reference.values == (MAPDATA,)
    assert reference.raw_line == raw_map


def test_missing_mapdata_leaves_references_empty(tmp_path: Path) -> None:
    path = tmp_path / "without-map.bbl"
    _write_log(
        path,
        "log-no-map\n"
        "20010101010101.000001,POSI,RobotPose,1,2,3\n"
        "20010101010101.000002,BULK,MAPDATA,\n"
        "20010101010101.000003,BULK,MAPDATA\n",
    )

    log = read_bbl(path)

    assert len(log.robot_poses) == 1
    assert log.map_references == ()
    assert len(log.records_by_subtype("MAPDATA")) == 2


def test_unknown_record_keeps_original_text(tmp_path: Path) -> None:
    path = tmp_path / "unknown.bbl"
    raw_line = "20010101010101.000001,XYZ,UnknownType, value1, value2"
    _write_log(path, f"test_log\n{raw_line}\n")

    record = read_bbl(path).records[0]

    assert record.record_type == "XYZ"
    assert record.subtype == "UnknownType"
    assert record.values == ("value1", "value2")
    assert record.raw_line == raw_line


def test_non_numeric_pose_values_stay_text(tmp_path: Path) -> None:
    path = tmp_path / "bad-pose.bbl"
    _write_log(
        path,
        "log-bad-pose\n"
        "20010101010101.000001,POSI,RobotPose,abc,def,ghi\n",
    )

    pose = read_bbl(path).robot_poses[0]

    assert pose.values == ("abc", "def", "ghi")
    assert pose.x is None
    assert pose.y is None
    assert pose.heading is None
    assert pose.is_position_record is False


def test_short_and_blank_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "skipped.bbl"
    raw_pose = "20010101010101.000004,POSI,RobotPose,4,5,6"
    _write_log(
        path,
        "log-skip\n"
        "\n"
        "too,short\n"
        f"{raw_pose}\n",
    )

    log = read_bbl(path)

    assert len(log.records) == 1
    assert log.records[0].raw_line == raw_pose
    assert log.records[0].line_number == 4


def test_empty_log_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.bbl"
    _write_log(path, "")

    with pytest.raises(ValueError, match="Empty BBL log"):
        read_bbl(path)


def test_read_bbl_bytes_matches_read_bbl(tmp_path: Path) -> None:
    path = tmp_path / "same.bbl"
    text = (
        "20010101010101_U_101\n"
        "20010101010101.000001,POSI,RobotPose,1180,53,-30\n"
        "20010101010101.000002,POSF,RobotPose,10,20,30\n"
        "20010101010101.000003,XYZ,UnknownType, value1, value2\n"
        f"20010101010121.000001,BULK,MAPDATA,{MAPDATA}\n"
        "\n"
        "too,short\n"
    )
    _write_log(path, text)

    from_path = read_bbl(path)
    from_bytes = read_bbl_bytes(text.encode("ascii"))

    assert from_bytes.log_id == from_path.log_id
    assert from_bytes.records == from_path.records
    assert from_bytes.map_references == from_path.map_references
    assert from_bytes.source is None
    assert from_path.source == path


def test_read_bbl_bytes_parses_posi_and_posf() -> None:
    text = (
        "log-positions\n"
        "20010101010101.000001,POSI,RobotPose,1180,53,-30\n"
        "20010101010101.000002,POSF,RobotPose, -4, 8, 90\n"
    )
    log = read_bbl_bytes(text.encode("ascii"))
    posi, posf = log.records

    assert posi.record_type == "POSI"
    assert posi.subtype == "RobotPose"
    assert (posi.x, posi.y, posi.heading) == (1180, 53, -30)
    assert posi.is_position_record is True
    assert posf.record_type == "POSF"
    assert (posf.x, posf.y, posf.heading) == (-4, 8, 90)
    assert posf.is_position_record is True


def test_read_bbl_bytes_keeps_unknown_records() -> None:
    raw_line = "20010101010101.000001,XYZ,UnknownType, value1, value2"
    log = read_bbl_bytes(f"test_log\n{raw_line}\n".encode("ascii"))
    record = log.records[0]

    assert record.record_type == "XYZ"
    assert record.subtype == "UnknownType"
    assert record.values == ("value1", "value2")
    assert record.raw_line == raw_line
    assert record.x is None


def test_read_bbl_bytes_rejects_empty_payload() -> None:
    with pytest.raises(ValueError, match="Empty BBL log"):
        read_bbl_bytes(b"")


def test_read_bbl_bytes_skips_invalid_lines_and_non_numeric_poses() -> None:
    text = (
        "log-invalid\n"
        "\n"
        "too,short\n"
        "20010101010101.000001,POSI,RobotPose,abc,def,ghi\n"
        "20010101010101.000002,POSI,RobotPose,1,2,3\n"
    )
    log = read_bbl_bytes(text.encode("ascii"))

    assert len(log.records) == 2
    assert log.records[0].x is None
    assert log.records[0].is_position_record is False
    assert log.records[1].x == 1
    assert log.records[1].line_number == 5
