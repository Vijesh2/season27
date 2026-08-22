from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FootballResult, SeasonTeam, Team
from app.results.source import ExternalResult, ResultsSourceError
from app.standings.source import normalize_team_name


@dataclass(frozen=True)
class ImportSummary:
    inserted: int
    updated: int


def _season_team_map(session: Session, season_id: int) -> dict[str, Team]:
    teams = session.scalars(
        select(Team).join(SeasonTeam).where(SeasonTeam.season_id == season_id)
    ).all()
    mapping: dict[str, Team] = {}
    for team in teams:
        mapping[team.source_identity] = team
        mapping[normalize_team_name(team.name)] = team
    return mapping


def _resolve_team(mapping: dict[str, Team], identity: str, name: str) -> Team:
    team = mapping.get(identity) or mapping.get(normalize_team_name(name))
    if team is None:
        raise ResultsSourceError(f"BBC result team is not in this season: {name}.")
    return team


def import_results(
    session: Session,
    season_id: int,
    results: tuple[ExternalResult, ...],
    checked_at: datetime,
) -> ImportSummary:
    mapping = _season_team_map(session, season_id)
    resolved = [
        (
            item,
            _resolve_team(mapping, item.home_identity, item.home_name),
            _resolve_team(mapping, item.away_identity, item.away_name),
        )
        for item in results
    ]
    inserted = 0
    updated = 0
    for item, home, away in resolved:
        stored = session.scalar(
            select(FootballResult).where(FootballResult.source_event_id == item.event_id)
        )
        values = {
            "competition": item.competition,
            "home_team_id": home.id,
            "away_team_id": away.id,
            "home_score": item.home_score,
            "away_score": item.away_score,
            "scheduled_at": item.scheduled_at,
            "completed_at": item.completed_at,
            "event_status": item.status,
            "source_url": item.source_url,
            "last_checked_at": checked_at,
            "source_metadata": item.source_metadata,
        }
        if stored is None:
            session.add(
                FootballResult(
                    source_event_id=item.event_id,
                    first_seen_at=checked_at,
                    **values,
                )
            )
            inserted += 1
        else:
            for key, value in values.items():
                setattr(stored, key, value)
            updated += 1
    session.commit()
    return ImportSummary(inserted=inserted, updated=updated)
