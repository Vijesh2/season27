from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.bulletins import BULLETIN_TITLE
from app.bulletins.fact_pack import FactPack, MatchFact, SnapshotFact
from app.bulletins.service import (
    InvalidBulletin,
    get_published_bulletins,
    publish_bulletin,
    save_draft,
    suppress_bulletin,
    update_bulletin,
    validate_body,
)
from app.db.models import AuditEvent, Bulletin, BulletinMatch, FootballResult, Player
from app.db.session import create_database_engine, create_schema
from app.seasons import seed_development_season
from app.teams.service import seed_fixed_teams

BODY = (
    "Arsenal’s win launched Alex up the table, while Chelsea’s defending left Sam "
    "checking whether predictions can be returned under warranty."
)


def prepared(path: Path) -> tuple[Session, Player, FootballResult, FactPack]:
    engine = create_database_engine(f"sqlite:///{path}")
    create_schema(engine)
    session = Session(engine, expire_on_commit=False)
    season = seed_development_season(session)
    roster = seed_fixed_teams(session, season)
    now = datetime(2026, 8, 24, 6, tzinfo=UTC)
    player = Player(
        display_name="Bulletin editor",
        login_code_hash="unused",
        is_admin=True,
        is_active=True,
        failed_login_count=0,
        created_at=now,
        updated_at=now,
    )
    session.add(player)
    session.flush()
    result = FootballResult(
        source_event_id="s-bulletin",
        competition="Premier League",
        home_team_id=roster[1].team_id,
        away_team_id=roster[5].team_id,
        home_score=3,
        away_score=1,
        scheduled_at=datetime(2026, 8, 23, 15, tzinfo=UTC),
        event_status="PostEvent",
        source_url="https://example.test/s-bulletin",
        first_seen_at=now,
        last_checked_at=now,
        source_metadata={"status": "FT"},
    )
    session.add(result)
    session.commit()
    pack = FactPack(
        bulletin_title=BULLETIN_TITLE,
        season_id=season.id,
        season_name=season.name,
        period_start="2026-08-17T05:00:00Z",
        period_end="2026-08-24T05:00:00Z",
        baseline_snapshot=SnapshotFact(1, 1, "2026-08-17T05:00:00Z"),
        current_snapshot=SnapshotFact(2, 2, "2026-08-24T05:00:00Z"),
        matches=(
            MatchFact(
                result_id=result.id,
                event_id=result.source_event_id,
                home_team="Arsenal",
                away_team="Chelsea",
                home_score=3,
                away_score=1,
                played_at="2026-08-23T15:00:00Z",
                source_url=result.source_url,
                evidence="period_context_only",
            ),
        ),
        prediction_changes=(),
        period_player_impacts=(),
        verified_match_impacts=(),
        claim_rules=("Use exact supplied facts.",),
    )
    return session, player, result, pack


def test_draft_edit_publish_suppress_and_republish_lifecycle(tmp_path: Path) -> None:
    session, player, result, pack = prepared(tmp_path / "lifecycle.db")
    now = datetime(2026, 8, 24, 6, tzinfo=UTC)
    with session:
        bulletin = save_draft(session, pack, BODY, player.id, now)
        assert bulletin.status == "draft"
        assert bulletin.title == BULLETIN_TITLE
        assert bulletin.slug == "monday-morning-banter-2026-08-24"
        assert session.scalar(select(func.count(BulletinMatch.id))) == 1
        assert bulletin.matches[0].football_result_id == result.id

        revised = BODY + " The spreadsheet has requested compassionate leave."
        update_bulletin(session, bulletin, revised, player.id, now)
        assert bulletin.body == revised

        publish_bulletin(session, bulletin, player.id, now)
        assert bulletin.status == "published"
        assert get_published_bulletins(session, pack.season_id) == [bulletin]

        suppress_bulletin(session, bulletin, player.id, now)
        assert bulletin.status == "suppressed"
        assert get_published_bulletins(session, pack.season_id) == []

        publish_bulletin(session, bulletin, player.id, now)
        assert bulletin.status == "published"
        event_types = set(session.scalars(select(AuditEvent.event_type)))
        assert {
            "bulletin_draft_created",
            "bulletin_updated",
            "bulletin_published",
            "bulletin_suppressed",
        } <= event_types


def test_duplicate_period_and_invalid_transitions_are_rejected(tmp_path: Path) -> None:
    session, player, _result, pack = prepared(tmp_path / "invalid.db")
    now = datetime(2026, 8, 24, 6, tzinfo=UTC)
    with session:
        bulletin = save_draft(session, pack, BODY, player.id, now)
        with pytest.raises(InvalidBulletin, match="already exists"):
            save_draft(session, pack, BODY, player.id, now)
        with pytest.raises(InvalidBulletin, match="published"):
            suppress_bulletin(session, bulletin, player.id, now)
        publish_bulletin(session, bulletin, player.id, now)
        with pytest.raises(InvalidBulletin, match="draft or suppressed"):
            publish_bulletin(session, bulletin, player.id, now)


def test_body_limits_and_unavailable_match_are_rejected(tmp_path: Path) -> None:
    assert validate_body("  " + BODY + "  ") == BODY
    with pytest.raises(InvalidBulletin, match="at least 15"):
        validate_body("Too short for publication")
    with pytest.raises(InvalidBulletin, match="no more than 120"):
        validate_body(" ".join(["word"] * 121))

    session, player, result, pack = prepared(tmp_path / "missing-result.db")
    with session:
        session.delete(result)
        session.commit()
        with pytest.raises(InvalidBulletin, match="unavailable"):
            save_draft(
                session,
                pack,
                BODY,
                player.id,
                datetime(2026, 8, 24, 6, tzinfo=UTC),
            )
        assert session.scalar(select(func.count(Bulletin.id))) == 0
