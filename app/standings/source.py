import re
from dataclasses import dataclass
from typing import Protocol

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag


class StandingsSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExternalStanding:
    identity: str
    name: str
    position: int
    played: int
    points: int
    goal_difference: int


@dataclass(frozen=True)
class SourceTable:
    rows: tuple[ExternalStanding, ...]
    is_final: bool


class StandingsSource(Protocol):
    name: str

    def fetch(self) -> SourceTable: ...


def normalize_team_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    normalized = normalized.removesuffix(" fc").strip()
    aliases = {
        "brighton": "brighton and hove albion",
        "man city": "manchester city",
        "man utd": "manchester united",
        "nott m forest": "nottingham forest",
        "nottm forest": "nottingham forest",
        "spurs": "tottenham hotspur",
    }
    return aliases.get(normalized, normalized)


def _number(row: Tag, label: str) -> int:
    cell = row.find("td", attrs={"aria-label": label})
    if cell is None:
        raise StandingsSourceError("A standings row is incomplete.")
    try:
        return int(cell.get_text(strip=True))
    except ValueError as error:
        raise StandingsSourceError("A standings value is invalid.") from error


def parse_bbc_table(html: str) -> SourceTable:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", attrs={"data-testid": "football-table"})
    if table is None:
        raise StandingsSourceError("The standings table was not found.")
    parsed: list[ExternalStanding] = []
    for row in table.find_all("tr"):
        team_cell = row.find("td", attrs={"aria-label": "Team"})
        if team_cell is None:
            continue
        badge = team_cell.find(  # type: ignore[call-overload]
            attrs={"data-testid": re.compile(r"^badge-container-")}
        )
        name_element = team_cell.find("span", class_=re.compile(r"VisuallyHidden"))
        rank_element = team_cell.find("span", class_=re.compile(r"-Rank"))
        if badge is None or name_element is None or rank_element is None:
            raise StandingsSourceError("A standings row is incomplete.")

        identity = str(badge["data-testid"]).removeprefix("badge-container-")
        try:
            position = int(rank_element.get_text(strip=True))
        except ValueError as error:
            raise StandingsSourceError("A standings position is invalid.") from error
        parsed.append(
            ExternalStanding(
                identity=identity,
                name=name_element.get_text(strip=True),
                position=position,
                played=_number(row, "Played"),
                points=_number(row, "Points"),
                goal_difference=_number(row, "Goal Difference"),
            )
        )
    if len(parsed) != 20:
        raise StandingsSourceError("The standings table is incomplete.")
    return SourceTable(rows=tuple(parsed), is_final=all(row.played >= 38 for row in parsed))


class BBCStandingsSource:
    name = "bbc"

    def __init__(self, url: str, connect_timeout: float, read_timeout: float) -> None:
        self.url = url
        self.timeout = (connect_timeout, read_timeout)

    def fetch(self) -> SourceTable:
        try:
            response = requests.get(
                self.url,
                timeout=self.timeout,
                headers={"User-Agent": "Season27/0.1 standings importer"},
            )
            response.raise_for_status()
            return parse_bbc_table(response.text)
        except (requests.RequestException, StandingsSourceError) as error:
            raise StandingsSourceError("Standings could not be refreshed.") from error


class DevelopmentStandingsSource:
    name = "development"

    def __init__(self, rows: tuple[ExternalStanding, ...]) -> None:
        self.rows = rows

    def fetch(self) -> SourceTable:
        return SourceTable(self.rows, is_final=all(row.played >= 38 for row in self.rows))
