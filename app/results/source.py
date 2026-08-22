import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

LONDON = ZoneInfo("Europe/London")


class ResultsSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExternalResult:
    event_id: str
    competition: str
    home_identity: str
    home_name: str
    away_identity: str
    away_name: str
    home_score: int
    away_score: int
    scheduled_at: datetime
    completed_at: datetime | None
    status: str
    source_url: str
    source_metadata: dict[str, object]


def dates_in_period(period_start: datetime, period_end: datetime) -> tuple[date, ...]:
    if period_start.tzinfo is None or period_end.tzinfo is None:
        raise ValueError("Result periods must use timezone-aware timestamps.")
    if period_end <= period_start:
        raise ValueError("The result period end must be after its start.")
    first = period_start.astimezone(LONDON).date()
    last = (period_end.astimezone(LONDON) - timedelta(microseconds=1)).date()
    return tuple(first + timedelta(days=offset) for offset in range((last - first).days + 1))


def _objects(value: object) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def _has_event_collection(value: object) -> bool:
    return any(isinstance(item.get("events"), list) for item in _objects(value))


def _initial_data(html: str) -> object:
    soup = BeautifulSoup(html, "html.parser")
    prefix = "window.__INITIAL_DATA__="
    script = next(
        (
            item.get_text()
            for item in soup.find_all("script")
            if item.get_text().lstrip().startswith(prefix)
        ),
        None,
    )
    if script is None:
        raise ResultsSourceError("BBC result data was not found.")
    expression = script.strip()[len(prefix) :].removesuffix(";")
    try:
        encoded = json.loads(expression)
        return json.loads(encoded) if isinstance(encoded, str) else encoded
    except (json.JSONDecodeError, TypeError) as error:
        raise ResultsSourceError("BBC result data is malformed.") from error


def _datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ResultsSourceError("A BBC result timestamp is missing.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ResultsSourceError("A BBC result timestamp is invalid.") from error
    if parsed.tzinfo is None:
        raise ResultsSourceError("A BBC result timestamp has no timezone.")
    return parsed.astimezone(UTC)


def _team(event: dict[str, Any], alignment: str) -> tuple[str, str, int]:
    team = event.get(alignment)
    if not isinstance(team, dict):
        raise ResultsSourceError("A completed BBC result has incomplete teams.")
    name = team.get("fullName")
    urn = team.get("urn")
    score = team.get("score")
    if not isinstance(name, str) or not isinstance(urn, str):
        raise ResultsSourceError("A completed BBC result has incomplete teams.")
    if not isinstance(score, (str, int)) or isinstance(score, bool):
        raise ResultsSourceError("A completed BBC result has an invalid score.")
    try:
        parsed_score = int(score)
    except (TypeError, ValueError) as error:
        raise ResultsSourceError("A completed BBC result has an invalid score.") from error
    return urn.rsplit(":", 1)[-1], name, parsed_score


def parse_bbc_results(
    html: str,
    source_url: str,
    period_start: datetime,
    period_end: datetime,
) -> tuple[ExternalResult, ...]:
    if period_start.tzinfo is None or period_end.tzinfo is None or period_end <= period_start:
        raise ValueError("A valid timezone-aware result period is required.")
    initial_data = _initial_data(html)
    if not _has_event_collection(initial_data):
        raise ResultsSourceError("No BBC football event collection was found.")
    results: dict[str, ExternalResult] = {}
    for event in _objects(initial_data):
        event_id = event.get("id")
        tournament = event.get("tournament")
        if not isinstance(event_id, str) or not event_id.startswith("s-"):
            continue
        if not isinstance(tournament, dict):
            continue
        if tournament.get("name") != "Premier League" or event.get("status") != "PostEvent":
            continue
        scheduled_at = _datetime(event.get("startDateTime"))
        if not period_start.astimezone(UTC) <= scheduled_at < period_end.astimezone(UTC):
            continue
        home_identity, home_name, home_score = _team(event, "home")
        away_identity, away_name, away_score = _team(event, "away")
        completed_value = event.get("endDateTime")
        completed_at = _datetime(completed_value) if completed_value is not None else None
        results[event_id] = ExternalResult(
            event_id=event_id,
            competition="Premier League",
            home_identity=home_identity,
            home_name=home_name,
            away_identity=away_identity,
            away_name=away_name,
            home_score=home_score,
            away_score=away_score,
            scheduled_at=scheduled_at,
            completed_at=completed_at,
            status="PostEvent",
            source_url=source_url,
            source_metadata={
                "event_grouping_label": str(event.get("eventGroupingLabel", "")),
                "status_comment": event.get("statusComment", {}),
            },
        )
    return tuple(sorted(results.values(), key=lambda item: (item.scheduled_at, item.event_id)))


class BBCResultsSource:
    def __init__(
        self,
        base_url: str,
        connect_timeout: float,
        read_timeout: float,
        retry_attempts: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = (connect_timeout, read_timeout)
        self.retry_attempts = max(1, retry_attempts)

    def fetch(self, period_start: datetime, period_end: datetime) -> tuple[ExternalResult, ...]:
        results: dict[str, ExternalResult] = {}
        try:
            days = dates_in_period(period_start, period_end)
            for day in days:
                url = f"{self.base_url}/{day.isoformat()}"
                response: requests.Response | None = None
                for attempt in range(self.retry_attempts):
                    try:
                        response = requests.get(
                            url,
                            timeout=self.timeout,
                            headers={"User-Agent": "Season27/0.1 results importer"},
                        )
                        response.raise_for_status()
                        break
                    except requests.RequestException:
                        if attempt + 1 == self.retry_attempts:
                            raise
                if response is None:  # pragma: no cover - defensive invariant
                    raise ResultsSourceError("BBC results could not be refreshed.")
                for result in parse_bbc_results(response.text, url, period_start, period_end):
                    results[result.event_id] = result
        except (requests.RequestException, ResultsSourceError) as error:
            raise ResultsSourceError("BBC results could not be refreshed.") from error
        return tuple(sorted(results.values(), key=lambda item: (item.scheduled_at, item.event_id)))
