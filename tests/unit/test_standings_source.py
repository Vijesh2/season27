from pathlib import Path

import pytest
import requests

from app.standings.source import (
    BBCStandingsSource,
    StandingsSourceError,
    normalize_team_name,
    parse_bbc_table,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "bbc_table.html"


def test_bbc_fixture_parses_all_rows_and_final_state() -> None:
    table = parse_bbc_table(FIXTURE.read_text())
    assert len(table.rows) == 20
    assert table.rows[0].identity == "afc-bournemouth"
    assert table.rows[0].name == "AFC Bournemouth"
    assert (table.rows[0].position, table.rows[0].played, table.rows[0].points) == (1, 38, 81)
    assert table.is_final


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Man City", "manchester city"),
        ("Manchester United FC", "manchester united"),
        ("Nott'm Forest", "nottingham forest"),
        ("Brighton", "brighton and hove albion"),
    ],
)
def test_team_name_normalization(value: str, expected: str) -> None:
    assert normalize_team_name(value) == expected


@pytest.mark.parametrize(
    "html",
    [
        "<html></html>",
        '<table data-testid="football-table"></table>',
        FIXTURE.read_text().replace('<td aria-label="Points">81</td>', "", 1),
        FIXTURE.read_text().replace(">38</td>", ">invalid</td>", 1),
    ],
)
def test_parser_rejects_missing_incomplete_and_malformed_tables(html: str) -> None:
    with pytest.raises(StandingsSourceError):
        parse_bbc_table(html)


def test_bbc_source_hides_transport_details(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> object:
        raise requests.Timeout("private upstream detail")

    monkeypatch.setattr(requests, "get", fail)
    source = BBCStandingsSource("https://example.invalid", 1, 2)
    with pytest.raises(StandingsSourceError, match="could not be refreshed") as raised:
        source.fetch()
    assert "private upstream" not in str(raised.value)
