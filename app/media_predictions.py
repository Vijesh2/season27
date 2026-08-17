from dataclasses import dataclass


@dataclass(frozen=True)
class MediaPrediction:
    publisher: str
    published_date: str
    source_url: str
    teams: tuple[str, ...]


MEDIA_PREDICTIONS = (
    MediaPrediction(
        publisher="The Athletic",
        published_date="17 August 2026",
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
)
