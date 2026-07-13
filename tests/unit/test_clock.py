from datetime import datetime

from app.clock import LONDON, clock_from_iso


def test_naive_override_is_interpreted_in_london() -> None:
    now = clock_from_iso("2026-08-20T23:00:00")()
    assert now == datetime(2026, 8, 20, 23, tzinfo=LONDON)


def test_utc_override_is_converted_to_bst() -> None:
    now = clock_from_iso("2026-08-20T22:00:00+00:00")()
    assert now.hour == 23
    assert now.tzname() == "BST"
