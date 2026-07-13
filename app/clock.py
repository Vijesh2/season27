from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
Clock = Callable[[], datetime]


def system_clock() -> datetime:
    return datetime.now(tz=LONDON)


def clock_from_iso(value: str | None) -> Clock:
    if value is None:
        return system_clock

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LONDON)
    fixed = parsed.astimezone(LONDON)
    return lambda: fixed
