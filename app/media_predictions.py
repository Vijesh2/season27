from dataclasses import dataclass


@dataclass(frozen=True)
class MediaPrediction:
    publisher: str
    date_note: str
    source_url: str
    teams: tuple[str, ...]
    expected_points: tuple[str, ...] | None = None


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
        expected_points=(
            "73.35",
            "66.66",
            "60.97",
            "59.04",
            "56.79",
            "55.73",
            "53.84",
            "52.86",
            "52.25",
            "50.73",
            "50.29",
            "48.87",
            "48.49",
            "47.63",
            "46.74",
            "45.83",
            "45.26",
            "44.21",
            "43.48",
            "40.49",
        ),
    ),
)
