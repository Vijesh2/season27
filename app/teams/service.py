from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Season, SeasonTeam, Team


@dataclass(frozen=True)
class TeamData:
    name: str
    short_name: str
    slug: str
    source_identity: str


FIXED_2026_27_TEAMS = (
    TeamData("AFC Bournemouth", "Bournemouth", "afc-bournemouth", "afc-bournemouth"),
    TeamData("Arsenal", "Arsenal", "arsenal", "arsenal"),
    TeamData("Aston Villa", "Aston Villa", "aston-villa", "aston-villa"),
    TeamData("Brentford", "Brentford", "brentford", "brentford"),
    TeamData("Brighton & Hove Albion", "Brighton", "brighton", "brighton-and-hove-albion"),
    TeamData("Chelsea", "Chelsea", "chelsea", "chelsea"),
    TeamData("Coventry City", "Coventry", "coventry-city", "coventry-city"),
    TeamData("Crystal Palace", "Crystal Palace", "crystal-palace", "crystal-palace"),
    TeamData("Everton", "Everton", "everton", "everton"),
    TeamData("Fulham", "Fulham", "fulham", "fulham"),
    TeamData("Hull City", "Hull", "hull-city", "hull-city"),
    TeamData("Ipswich Town", "Ipswich", "ipswich-town", "ipswich-town"),
    TeamData("Leeds United", "Leeds", "leeds-united", "leeds-united"),
    TeamData("Liverpool", "Liverpool", "liverpool", "liverpool"),
    TeamData("Manchester City", "Man City", "manchester-city", "manchester-city"),
    TeamData("Manchester United", "Man Utd", "manchester-united", "manchester-united"),
    TeamData("Newcastle United", "Newcastle", "newcastle-united", "newcastle-united"),
    TeamData("Nottingham Forest", "Nott'm Forest", "nottingham-forest", "nottingham-forest"),
    TeamData("Sunderland", "Sunderland", "sunderland", "sunderland"),
    TeamData("Tottenham Hotspur", "Tottenham", "tottenham-hotspur", "tottenham-hotspur"),
)


def validate_fixed_teams(teams: tuple[TeamData, ...] | list[TeamData]) -> None:
    errors: list[str] = []
    if len(teams) != 20:
        errors.append("The roster must contain exactly 20 teams.")
    checks = (("name", "team names"), ("slug", "slugs"), ("source_identity", "source identities"))
    for attribute, label in checks:
        values = [getattr(team, attribute).casefold() for team in teams]
        if len(values) != len(set(values)):
            errors.append(f"The roster contains duplicate {label}.")
    if errors:
        raise ValueError(" ".join(errors))


def seed_fixed_teams(session: Session, season: Season) -> list[SeasonTeam]:
    existing = get_season_teams(session, season.id)
    if existing:
        return existing
    validate_fixed_teams(FIXED_2026_27_TEAMS)
    for order, item in enumerate(FIXED_2026_27_TEAMS, start=1):
        team = session.scalar(select(Team).where(Team.source_identity == item.source_identity))
        if team is None:
            team = Team(
                name=item.name,
                short_name=item.short_name,
                slug=item.slug,
                source_identity=item.source_identity,
            )
            session.add(team)
            session.flush()
        session.add(SeasonTeam(season_id=season.id, team_id=team.id, display_order=order))
    session.commit()
    return get_season_teams(session, season.id)


def get_season_teams(session: Session, season_id: int) -> list[SeasonTeam]:
    statement = (
        select(SeasonTeam)
        .options(selectinload(SeasonTeam.team))
        .where(SeasonTeam.season_id == season_id)
        .order_by(SeasonTeam.display_order)
    )
    return list(session.scalars(statement))
