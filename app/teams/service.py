from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.auth.service import audit
from app.db.models import Season, SeasonTeam, Team
from app.seasons import london

ROSTER_SOURCE = (
    "Premier League 2026/27 official membership announcement, 5 June 2026 — "
    "https://www.premierleague.com/en/news/4673099/"
    "the-202627-premier-league-season-officially-starts"
)


@dataclass(frozen=True)
class TeamData:
    name: str
    short_name: str
    slug: str
    source_identity: str


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[str, ...]


OFFICIAL_2026_27_TEAMS = (
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


def validate_roster(teams: tuple[TeamData, ...] | list[TeamData]) -> ValidationResult:
    errors: list[str] = []
    if len(teams) != 20:
        errors.append("The roster must contain exactly 20 teams.")
    checks = (("name", "team names"), ("slug", "slugs"), ("source_identity", "source identities"))
    for attribute, label in checks:
        values = [getattr(team, attribute).casefold() for team in teams]
        if len(values) != len(set(values)):
            errors.append(f"The roster contains duplicate {label}.")
    return ValidationResult(not errors, tuple(errors))


def import_roster(
    session: Session,
    season: Season,
    teams: tuple[TeamData, ...] | list[TeamData],
    now: datetime,
    source: str = ROSTER_SOURCE,
) -> ValidationResult:
    validation = validate_roster(teams)
    if not validation.valid:
        return validation
    if season.roster_approved_at is not None or now >= london(season.game_opens_at):
        return ValidationResult(False, ("The roster is already approved or locked.",))

    session.execute(delete(SeasonTeam).where(SeasonTeam.season_id == season.id))
    for order, item in enumerate(teams, start=1):
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
    season.roster_source = source
    season.roster_imported_at = now
    audit(session, "team_roster_imported", now, metadata={"season_id": season.id, "count": 20})
    session.commit()
    return validation


def get_roster(session: Session, season_id: int) -> list[SeasonTeam]:
    statement = (
        select(SeasonTeam)
        .options(selectinload(SeasonTeam.team))
        .where(SeasonTeam.season_id == season_id)
        .order_by(SeasonTeam.display_order)
    )
    return list(session.scalars(statement))


def approve_roster(session: Session, season: Season, admin_player_id: int, now: datetime) -> bool:
    if season.roster_approved_at is not None:
        return True
    if now >= london(season.game_opens_at):
        return False
    roster = get_roster(session, season.id)
    data = [
        TeamData(item.team.name, item.team.short_name, item.team.slug, item.team.source_identity)
        for item in roster
    ]
    if not validate_roster(data).valid:
        return False
    season.roster_approved_at = now
    audit(
        session,
        "team_roster_approved",
        now,
        admin_player_id,
        {"season_id": season.id, "count": 20},
    )
    session.commit()
    return True
