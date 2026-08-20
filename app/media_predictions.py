from dataclasses import dataclass


@dataclass(frozen=True)
class MediaPrediction:
    publisher: str
    date_note: str
    source_url: str
    teams: tuple[str, ...]


MEDIA_PREDICTIONS = (
    MediaPrediction(
        publisher="The Athletic",
        date_note="Published 17 August 2026",
        source_url=(
            "https://www.nytimes.com/athletic/7510781/2026/08/17/"
            "premier-league-predictions-2026-2027/"
        ),
        teams=(
            "Arsenal",
            "Manchester City",
            "Chelsea",
            "Manchester United",
            "Liverpool",
            "Aston Villa",
            "Tottenham Hotspur",
            "Brighton & Hove Albion",
            "Nottingham Forest",
            "Newcastle United",
            "Brentford",
            "Leeds United",
            "AFC Bournemouth",
            "Crystal Palace",
            "Everton",
            "Sunderland",
            "Fulham",
            "Ipswich Town",
            "Coventry City",
            "Hull City",
        ),
    ),
    MediaPrediction(
        publisher="Opta Analyst",
        date_note="Updated 19 August 2026",
        source_url="https://theanalyst.com/competition/premier-league/table",
        teams=(
            "Arsenal",
            "Manchester City",
            "Liverpool",
            "Manchester United",
            "Aston Villa",
            "Chelsea",
            "Newcastle United",
            "Tottenham Hotspur",
            "Brighton & Hove Albion",
            "AFC Bournemouth",
            "Everton",
            "Brentford",
            "Nottingham Forest",
            "Crystal Palace",
            "Leeds United",
            "Fulham",
            "Sunderland",
            "Coventry City",
            "Ipswich Town",
            "Hull City",
        ),
    ),
    MediaPrediction(
        publisher="The Telegraph",
        date_note="Published 20 August 2026",
        source_url=(
            "https://www.telegraph.co.uk/football/2026/08/20/"
            "premier-league-table-data-arsenal-win-spurs-relegation/"
        ),
        teams=(
            "Arsenal",
            "Manchester City",
            "Liverpool",
            "Manchester United",
            "Aston Villa",
            "AFC Bournemouth",
            "Brighton & Hove Albion",
            "Nottingham Forest",
            "Newcastle United",
            "Brentford",
            "Chelsea",
            "Leeds United",
            "Everton",
            "Fulham",
            "Crystal Palace",
            "Tottenham Hotspur",
            "Sunderland",
            "Coventry City",
            "Ipswich Town",
            "Hull City",
        ),
    ),
)
