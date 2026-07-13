from dataclasses import replace
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.auth.service import seed_development_players
from app.clock import LONDON
from app.db.models import AuditEvent, Base, SeasonTeam
from app.seasons import seed_development_season
from app.teams.service import (
    OFFICIAL_2026_27_TEAMS,
    approve_roster,
    get_roster,
    import_roster,
    validate_roster,
)


def database() -> tuple[Session, object, object]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    now = datetime(2026, 7, 13, tzinfo=LONDON)
    season = seed_development_season(session)
    admin = seed_development_players(session, now)[0]
    return session, season, admin


def test_official_fixture_has_twenty_unique_teams() -> None:
    result = validate_roster(OFFICIAL_2026_27_TEAMS)
    assert result.valid
    assert result.errors == ()


def test_validation_rejects_wrong_count_and_duplicates() -> None:
    short = list(OFFICIAL_2026_27_TEAMS[:-1])
    assert not validate_roster(short).valid
    duplicate = list(OFFICIAL_2026_27_TEAMS)
    duplicate[-1] = replace(
        duplicate[-1],
        name=duplicate[0].name,
        slug=duplicate[0].slug,
        source_identity=duplicate[0].source_identity,
    )
    errors = validate_roster(duplicate).errors
    assert "The roster contains duplicate team names." in errors
    assert "The roster contains duplicate slugs." in errors
    assert "The roster contains duplicate source identities." in errors


def test_invalid_import_does_not_partially_replace_roster() -> None:
    session, season, _ = database()
    result = import_roster(
        session, season, list(OFFICIAL_2026_27_TEAMS[:-1]), datetime(2026, 7, 13, tzinfo=LONDON)
    )
    assert not result.valid
    assert session.scalars(select(SeasonTeam)).all() == []


def test_import_and_approval_are_audited_and_idempotent() -> None:
    session, season, admin = database()
    now = datetime(2026, 7, 13, tzinfo=LONDON)
    assert import_roster(session, season, OFFICIAL_2026_27_TEAMS, now).valid
    assert len(get_roster(session, season.id)) == 20
    assert approve_roster(session, season, admin.id, now)
    assert approve_roster(session, season, admin.id, now)
    events = session.scalars(select(AuditEvent).order_by(AuditEvent.id)).all()
    assert [event.event_type for event in events] == [
        "team_roster_imported",
        "team_roster_approved",
    ]
    assert all("code" not in event.event_metadata for event in events)


def test_roster_cannot_import_or_approve_after_game_opens() -> None:
    session, season, admin = database()
    before = datetime(2026, 7, 13, tzinfo=LONDON)
    opened = datetime(2026, 8, 1, tzinfo=LONDON)
    assert import_roster(session, season, OFFICIAL_2026_27_TEAMS, before).valid
    assert not approve_roster(session, season, admin.id, opened)
    replacement = import_roster(session, season, OFFICIAL_2026_27_TEAMS, opened)
    assert not replacement.valid


def test_approved_roster_cannot_be_reimported() -> None:
    session, season, admin = database()
    now = datetime(2026, 7, 13, tzinfo=LONDON)
    assert import_roster(session, season, OFFICIAL_2026_27_TEAMS, now).valid
    assert approve_roster(session, season, admin.id, now)
    assert not import_roster(session, season, OFFICIAL_2026_27_TEAMS, now).valid
