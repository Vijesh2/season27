from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import requests

from app.results.source import (
    BBCResultsSource,
    ResultsSourceError,
    dates_in_period,
    parse_bbc_results,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "bbc_results.html"


def test_dates_cover_month_and_year_boundaries_without_extra_end_day() -> None:
    assert [item.isoformat() for item in dates_in_period(
        datetime(2026, 8, 31, 6, tzinfo=UTC),
        datetime(2026, 9, 2, 6, tzinfo=UTC),
    )] == ["2026-08-31", "2026-09-01", "2026-09-02"]
    assert [item.isoformat() for item in dates_in_period(
        datetime(2026, 12, 31, tzinfo=UTC),
        datetime(2027, 1, 1, tzinfo=UTC),
    )] == ["2026-12-31"]


def test_dates_reject_naive_or_reversed_periods() -> None:
    aware = datetime(2026, 9, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="timezone-aware"):
        dates_in_period(aware.replace(tzinfo=None), aware)
    with pytest.raises(ValueError, match="after"):
        dates_in_period(aware, aware)


def test_parser_selects_completed_premier_league_results_in_period() -> None:
    results = parse_bbc_results(
        FIXTURE.read_text(),
        "https://www.bbc.co.uk/sport/football/scores-fixtures/2026-08-31",
        datetime(2026, 8, 31, 6, tzinfo=UTC),
        datetime(2026, 9, 2, 6, tzinfo=UTC),
    )
    assert [item.event_id for item in results] == ["s-august", "s-september"]
    assert (results[0].home_name, results[0].away_name) == ("Arsenal", "Chelsea")
    assert (results[0].home_score, results[0].away_score) == (3, 1)
    assert results[0].home_identity == "arsenal"
    assert results[0].completed_at == datetime(2026, 8, 31, 17, 25, tzinfo=UTC)
    assert results[1].completed_at is None


@pytest.mark.parametrize(
    "html",
    [
        "<html></html>",
        "<script>window.__INITIAL_DATA__=not-json;</script>",
        '<script>window.__INITIAL_DATA__={"data":{"different":[]}};</script>',
        FIXTURE.read_text().replace('"score":"3"', '"score":"bad"', 1),
    ],
)
def test_parser_fails_closed_for_missing_or_malformed_data(html: str) -> None:
    with pytest.raises(ResultsSourceError):
        parse_bbc_results(
            html,
            "https://example.test/results",
            datetime(2026, 8, 31, tzinfo=UTC),
            datetime(2026, 9, 2, tzinfo=UTC),
        )


def test_parser_accepts_a_valid_day_with_no_events() -> None:
    assert parse_bbc_results(
        '<script>window.__INITIAL_DATA__={"data":{"events":[]}};</script>',
        "https://example.test/results",
        datetime(2026, 8, 31, tzinfo=UTC),
        datetime(2026, 9, 2, tzinfo=UTC),
    ) == ()


class StubResponse:
    text = FIXTURE.read_text()

    def raise_for_status(self) -> None:
        return None


def test_source_requests_every_date_and_deduplicates_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls: list[str] = []

    def get(url: str, **_kwargs: Any) -> StubResponse:
        urls.append(url)
        return StubResponse()

    monkeypatch.setattr(requests, "get", get)
    source = BBCResultsSource("https://example.test/results/", 1, 2, 1)
    results = source.fetch(
        datetime(2026, 8, 31, 6, tzinfo=UTC),
        datetime(2026, 9, 2, 6, tzinfo=UTC),
    )
    assert urls == [
        "https://example.test/results/2026-08-31",
        "https://example.test/results/2026-09-01",
        "https://example.test/results/2026-09-02",
    ]
    assert [item.event_id for item in results] == ["s-august", "s-september"]


def test_source_retries_and_hides_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def get(*_args: Any, **_kwargs: Any) -> StubResponse:
        nonlocal calls
        calls += 1
        raise requests.Timeout("private upstream detail")

    monkeypatch.setattr(requests, "get", get)
    source = BBCResultsSource("https://example.test/results", 1, 2, 2)
    with pytest.raises(ResultsSourceError, match="could not be refreshed") as raised:
        source.fetch(
            datetime(2026, 8, 31, tzinfo=UTC),
            datetime(2026, 9, 1, tzinfo=UTC),
        )
    assert calls == 2
    assert "private upstream" not in str(raised.value)
