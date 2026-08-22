import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.bulletins import BULLETIN_TITLE
from app.bulletins.fact_pack import FactPack, FactPackError, build_fact_pack
from app.db.models import (
    FootballResult,
    Player,
    Prediction,
    PredictionSnapshot,
    PredictionStatus,
)
from app.db.session import create_database_engine, create_schema
from app.seasons import seed_development_season
from app.standings.service import StandingInput, create_snapshot
from app.teams.service import seed_fixed_teams


def standings(
    team_ids: list[int],
    *,
    after_home_win: bool,
) -> list[StandingInput]:
    first, second, *others = team_ids
    leading = [
        StandingInput(
            team_id=second if after_home_win else first,
            position=1,
            played=2 if after_home_win else 1,
            points=4 if after_home_win else 3,
            goal_difference=2 if after_home_win else 1,
            goals_scored=3 if after_home_win else 2,
        ),
        StandingInput(
            team_id=first if after_home_win else second,
            position=2,
            played=2 if after_home_win else 1,
            points=3 if after_home_win else 1,
            goal_difference=-1 if after_home_win else 0,
            goals_scored=2 if after_home_win else 1,
        ),
    ]
    return leading + [
        StandingInput(
            team_id=team_id,
            position=position,
            played=1,
            points=40 - position,
            goal_difference=20 - position,
            goals_scored=30 - position,
        )
        for position, team_id in enumerate(others, start=3)
    ]


def add_player(
    session: Session,
    season_id: int,
    name: str,
    team_ids: list[int],
    order: list[int],
) -> Player:
    now = datetime(2026, 8, 20, tzinfo=UTC)
    player = Player(
        display_name=name,
        login_code_hash="unused",
        is_admin=False,
        is_active=True,
        failed_login_count=0,
        created_at=now,
        updated_at=now,
    )
    session.add(player)
    session.flush()
    session.add(
        PredictionStatus(
            player_id=player.id,
            season_id=season_id,
            submitted_at=now,
            submitted_order=order,
            locked_at=now,
        )
    )
    session.add_all(
        Prediction(
            player_id=player.id,
            season_id=season_id,
            team_id=team_id,
            predicted_position=position,
            updated_at=now,
        )
        for position, team_id in enumerate(order, start=1)
    )
    session.add(
        PredictionSnapshot(
            player_id=player.id,
            season_id=season_id,
            snapshot_type="deadline_lock",
            prediction_data=[
                {"team_id": team_id, "predicted_position": position}
                for position, team_id in enumerate(order, start=1)
            ],
            created_at=now,
        )
    )
    session.commit()
    return player


def prepared_database(path: Path) -> tuple[Session, int, list[int], int, int]:
    engine = create_database_engine(f"sqlite:///{path}")
    create_schema(engine)
    session = Session(engine, expire_on_commit=False)
    season = seed_development_season(session)
    roster = seed_fixed_teams(session, season)
    team_ids = [item.team_id for item in roster]
    add_player(session, season.id, "Before specialist", team_ids, team_ids)
    add_player(
        session,
        season.id,
        "After specialist",
        team_ids,
        [team_ids[1], team_ids[0], *team_ids[2:]],
    )
    before = create_snapshot(
        session,
        season.id,
        standings(team_ids, after_home_win=False),
        datetime(2026, 8, 24, 6),
        source="test",
    )
    after = create_snapshot(
        session,
        season.id,
        standings(team_ids, after_home_win=True),
        datetime(2026, 8, 24, 12),
        source="test",
    )
    return session, season.id, team_ids, before.id, after.id


def add_result(
    session: Session,
    team_ids: list[int],
    *,
    event_id: str = "s-verified",
    home_team_index: int = 1,
    away_team_index: int = 0,
    home_score: int = 2,
    away_score: int = 0,
    scheduled_at: datetime | None = None,
) -> FootballResult:
    checked = datetime(2026, 8, 24, 12)
    scheduled_at = scheduled_at or datetime(2026, 8, 24, 10, 30)
    result = FootballResult(
        source_event_id=event_id,
        competition="Premier League",
        home_team_id=team_ids[home_team_index],
        away_team_id=team_ids[away_team_index],
        home_score=home_score,
        away_score=away_score,
        scheduled_at=scheduled_at,
        completed_at=None,
        event_status="PostEvent",
        source_url=f"https://example.test/{event_id}",
        first_seen_at=checked,
        last_checked_at=checked,
        source_metadata={"status": "FT"},
    )
    session.add(result)
    session.commit()
    return result


def fact_pack(session: Session, season_id: int, before_id: int, after_id: int) -> FactPack:
    return build_fact_pack(
        session,
        season_id,
        datetime(2026, 8, 24, 4, tzinfo=UTC),
        datetime(2026, 8, 24, 13, tzinfo=UTC),
        baseline_snapshot_id=before_id,
        current_snapshot_id=after_id,
    )


def test_fact_pack_calculates_rank_score_and_verified_match_impacts(tmp_path: Path) -> None:
    session, season_id, team_ids, before_id, after_id = prepared_database(
        tmp_path / "verified.db"
    )
    with session:
        result = add_result(session, team_ids)
        pack = fact_pack(session, season_id, before_id, after_id)
        assert pack.bulletin_title == BULLETIN_TITLE
        assert pack.baseline_snapshot.id == before_id
        assert pack.current_snapshot.id == after_id
        assert len(pack.matches) == 1
        assert pack.matches[0].evidence == "verified_single_match_impact"
        assert pack.matches[0].home_score == 2 and pack.matches[0].away_score == 0
        assert len(pack.verified_match_impacts) == 1
        verified = pack.verified_match_impacts[0]
        assert verified.result_id == result.id
        changes = {item.display_name: item for item in pack.period_player_impacts}
        assert changes["Before specialist"].rank_change == -1
        assert changes["Before specialist"].score_change == 2
        assert changes["After specialist"].rank_change == 1
        assert changes["After specialist"].score_change == -2
        assert json.loads(json.dumps(pack.to_dict()))["bulletin_title"] == BULLETIN_TITLE


def test_multiple_results_are_context_not_individual_causation(tmp_path: Path) -> None:
    session, season_id, team_ids, before_id, after_id = prepared_database(
        tmp_path / "multiple.db"
    )
    with session:
        add_result(session, team_ids)
        add_result(
            session,
            team_ids,
            event_id="s-second",
            home_team_index=2,
            away_team_index=3,
            home_score=1,
            away_score=1,
            scheduled_at=datetime(2026, 8, 24, 11),
        )
        pack = fact_pack(session, season_id, before_id, after_id)
        assert pack.verified_match_impacts == ()
        assert {item.evidence for item in pack.matches} == {"period_context_only"}
        assert any("must not be assigned individual causation" in rule for rule in pack.claim_rules)


def test_scoreline_must_match_table_transition_for_causation(tmp_path: Path) -> None:
    session, season_id, team_ids, before_id, after_id = prepared_database(
        tmp_path / "wrong-score.db"
    )
    with session:
        add_result(session, team_ids, home_score=1, away_score=0)
        pack = fact_pack(session, season_id, before_id, after_id)
        assert pack.verified_match_impacts == ()
        assert pack.matches[0].evidence == "period_context_only"


def test_prediction_change_is_reported_and_blocks_match_causation(tmp_path: Path) -> None:
    session, season_id, team_ids, before_id, after_id = prepared_database(
        tmp_path / "prediction-change.db"
    )
    with session:
        result = add_result(session, team_ids)
        player = session.query(Player).filter_by(display_name="Before specialist").one()
        session.add(
            PredictionSnapshot(
                player_id=player.id,
                season_id=season_id,
                snapshot_type="post_swap",
                prediction_data=[
                    {"team_id": team_id, "predicted_position": position}
                    for position, team_id in enumerate(team_ids, start=1)
                ],
                created_at=datetime(2026, 8, 24, 10, 45),
            )
        )
        session.commit()
        pack = fact_pack(session, season_id, before_id, after_id)
        assert pack.verified_match_impacts == ()
        assert pack.matches[0].result_id == result.id
        assert pack.matches[0].evidence == "period_context_only"
        assert len(pack.prediction_changes) == 1
        assert pack.prediction_changes[0].display_name == "Before specialist"
        assert pack.prediction_changes[0].change_type == "post_swap"


def test_tied_players_remain_tied_and_unchanged_players_are_included(tmp_path: Path) -> None:
    session, season_id, team_ids, before_id, after_id = prepared_database(tmp_path / "ties.db")
    with session:
        add_player(session, season_id, "Matching specialist", team_ids, team_ids)
        add_result(session, team_ids)
        pack = fact_pack(session, season_id, before_id, after_id)
        impacts = {item.display_name: item for item in pack.period_player_impacts}
        assert impacts["Before specialist"].previous_rank == 1
        assert impacts["Matching specialist"].previous_rank == 1
        assert impacts["Before specialist"].current_rank == 2
        assert impacts["Matching specialist"].current_rank == 2


def test_fact_pack_requires_valid_period_and_allows_a_quiet_week(tmp_path: Path) -> None:
    session, season_id, _team_ids, before_id, _after_id = prepared_database(
        tmp_path / "missing.db"
    )
    with session:
        instant = datetime(2026, 8, 24, tzinfo=UTC)
        with pytest.raises(FactPackError, match="timezone-aware"):
            build_fact_pack(session, season_id, instant.replace(tzinfo=None), instant)
        pack = build_fact_pack(
            session,
            season_id,
            instant,
            instant.replace(hour=13),
            baseline_snapshot_id=before_id,
            current_snapshot_id=before_id,
        )
        assert pack.baseline_snapshot == pack.current_snapshot
        assert all(item.score_change == 0 for item in pack.period_player_impacts)
