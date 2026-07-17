from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
Clock = Callable[[], datetime]


class MutableClock:
    """A controllable clock for isolated browser tests and staging rehearsals."""

    def __init__(self, value: datetime) -> None:
        self.value = london_time(value)

    def __call__(self) -> datetime:
        return self.value

    def set(self, value: datetime) -> None:
        self.value = london_time(value)


def london_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=LONDON)
    return value.astimezone(LONDON)


def system_clock() -> datetime:
    return datetime.now(tz=LONDON)


def clock_from_iso(value: str | None) -> Clock:
    if value is None:
        return system_clock

    parsed = datetime.fromisoformat(value)
    fixed = london_time(parsed)
    return lambda: fixed
