"""latest_run: which listed cleaning run is the most recent."""

from __future__ import annotations

from lg_hombot.route_list import HistoricalRun, latest_run


def _run(stamp: str, run_id: str) -> HistoricalRun:
    return HistoricalRun(
        bbl_name=f"cleanlog{stamp}_U_{run_id}.bbl",
        blk_name=f"MAPDATA{stamp}_000001_{run_id}.blk",
        run_id=run_id,
        label=f"{stamp}-{run_id}",
    )


def test_no_run_means_no_latest() -> None:
    assert latest_run(()) is None


def test_newest_timestamp_wins_in_any_list_order() -> None:
    old = _run("20240101100000", "7")
    new = _run("20250304112233", "8")
    assert latest_run([old, new]) is new
    assert latest_run([new, old]) is new


def test_timestamp_beats_a_larger_run_id() -> None:
    """Run ids restart on some firmware, so they never outrank the date."""
    older_big_id = _run("20240101100000", "9000")
    newer_small_id = _run("20250101100000", "3")
    assert latest_run([older_big_id, newer_small_id]) is newer_small_id


def test_same_minute_uses_the_numeric_run_id() -> None:
    ten = _run("20250101100000", "10")
    nine = _run("20250101100000", "9")
    assert latest_run([ten, nine]) is ten
    assert latest_run([nine, ten]) is ten


def test_same_minute_and_id_falls_back_to_the_last_listed() -> None:
    first = _run("20250101100000", "abc")
    second = _run("20250101100000", "abd")
    assert latest_run([first, second]) is second
    assert latest_run([second, first]) is first


def test_accepts_any_iterable() -> None:
    run = _run("20250101100000", "1")
    assert latest_run(iter([run])) is run
