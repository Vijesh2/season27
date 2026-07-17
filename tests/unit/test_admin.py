from datetime import datetime, timedelta

import pytest
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.admin.service import (
    InvalidAdminAction,
    correct_prediction,
    correct_standings,
    reinstate_player,
    require_reason,
    reset_player_lock,
    reverse_swap,
    revoke_player_sessions,
    revoke_session,
    rotate_login_code,
    update_player,
    update_season_dates,
)
from app.auth.service import hasher, seed_development_players
from app.clock import LONDON
from app.db.models import (
    AppSession,
    AuditEvent,
    Base,
    LoginThrottle,
    PredictionSnapshot,
    PredictionStatus,
)
from app.predictions.service import ensure_draft, get_draft, process_deadline, submit_prediction
from app.seasons import seed_development_season
from app.standings.service import get_latest_snapshot, seed_development_snapshot
from app.swaps.service import apply_swap
from app.teams.service import seed_fixed_teams


def database() -> tuple[Session, object, list[object]]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    now = datetime(2026, 8, 2, tzinfo=LONDON)
    season = seed_development_season(session)
    seed_fixed_teams(session, season)
    players = seed_development_players(session, now)
    seed_development_snapshot(session, season.id, now)
    return session, season, players


def test_code_rotation_returns_once_hashes_code_and_revokes_sessions() -> None:
    session, _, players = database()
    now = datetime(2026, 8, 3, tzinfo=LONDON)
    player = players[1]  # type: ignore[index]
    session.add(
        AppSession(
            player_id=player.id,
            token_hash="a" * 64,
            csrf_token="csrf",
            expires_at=now + timedelta(days=1),
            created_at=now,
            last_seen_at=now,
        )
    )
    session.commit()
    code = rotate_login_code(session, players[0].id, player.id, now)  # type: ignore[index]
    assert len(code) == 4
    assert code not in player.login_code_hash
    assert hasher.verify(player.login_code_hash, code)
    stored_session = session.scalar(select(AppSession).where(AppSession.player_id == player.id))
    assert stored_session is not None and stored_session.revoked_at is not None
    event = session.scalar(select(AuditEvent).where(AuditEvent.event_type == "login_code_rotated"))
    assert event is not None and code not in str(event.event_metadata)
    with pytest.raises(VerifyMismatchError):
        hasher.verify(player.login_code_hash, "D002")


def test_player_update_validates_identity_and_self_deactivation() -> None:
    session, _, players = database()
    now = datetime(2026, 8, 3, tzinfo=LONDON)
    update_player(session, players[0].id, players[1].id, "Local Player", True, now)  # type: ignore[index]
    assert players[1].display_name == "Local Player"  # type: ignore[index]
    with pytest.raises(InvalidAdminAction, match="already in use"):
        update_player(  # type: ignore[index]
            session, players[0].id, players[1].id, players[0].display_name, True, now
        )
    with pytest.raises(InvalidAdminAction, match="own administrator"):
        update_player(  # type: ignore[index]
            session, players[0].id, players[0].id, players[0].display_name, False, now
        )
    with pytest.raises(InvalidAdminAction, match="Player not found"):
        update_player(session, players[0].id, 99999, "Missing", True, now)  # type: ignore[index]
    with pytest.raises(InvalidAdminAction, match="no more than 80"):
        update_player(session, players[0].id, players[1].id, "", True, now)  # type: ignore[index]


def test_lock_reset_and_session_revocation_are_audited() -> None:
    session, _, players = database()
    now = datetime(2026, 8, 3, tzinfo=LONDON)
    target = players[1]  # type: ignore[index]
    target.failed_login_count = 5
    target.locked_until = now + timedelta(minutes=15)
    session.add(
        LoginThrottle(
            key_hash="locked",
            failed_count=5,
            window_started_at=now,
            locked_until=now + timedelta(minutes=15),
        )
    )
    app_sessions = [
        AppSession(
            player_id=target.id,
            token_hash=letter * 64,
            csrf_token=f"csrf-{letter}",
            expires_at=now + timedelta(days=1),
            created_at=now,
            last_seen_at=now,
        )
        for letter in ("b", "c")
    ]
    session.add_all(app_sessions)
    session.commit()
    reset_player_lock(session, players[0].id, target.id, now)  # type: ignore[index]
    throttle = session.scalar(select(LoginThrottle).where(LoginThrottle.key_hash == "locked"))
    assert target.locked_until is None and target.failed_login_count == 0
    assert throttle is not None and throttle.locked_until is None
    revoke_session(session, players[0].id, app_sessions[0].id, now)  # type: ignore[index]
    assert app_sessions[0].revoked_at is not None
    assert revoke_player_sessions(session, players[0].id, target.id, now) == 1  # type: ignore[index]
    assert app_sessions[1].revoked_at is not None
    with pytest.raises(InvalidAdminAction, match="Session not found"):
        revoke_session(session, players[0].id, 99999, now)  # type: ignore[index]


def test_season_dates_are_editable_only_before_lock() -> None:
    session, season, players = database()
    now = datetime(2026, 8, 2, tzinfo=LONDON)
    opens = datetime(2026, 8, 3, tzinfo=LONDON)
    locks = datetime(2026, 8, 22, tzinfo=LONDON)
    update_season_dates(session, players[0].id, season, opens, locks, now)  # type: ignore[arg-type,index]
    assert season.game_opens_at == opens  # type: ignore[attr-defined]
    assert season.swap_windows[0].opens_at == locks  # type: ignore[attr-defined]
    with pytest.raises(InvalidAdminAction, match="after predictions lock"):
        update_season_dates(  # type: ignore[arg-type,index]
            session, players[0].id, season, opens, locks, locks
        )


def test_reinstatement_and_prediction_correction_create_audited_snapshots() -> None:
    session, season, players = database()
    deadline = datetime(2026, 8, 21, tzinfo=LONDON)
    process_deadline(session, season, deadline)  # type: ignore[arg-type]
    player = players[1]  # type: ignore[index]
    status = session.scalar(select(PredictionStatus).where(PredictionStatus.player_id == player.id))
    assert status is not None and status.excluded_at is not None
    team_ids = [item.team_id for item in seed_fixed_teams(session, season)]  # type: ignore[arg-type]
    reinstate_player(  # type: ignore[arg-type,index]
        session,
        players[0].id,
        player.id,
        season,
        team_ids,
        "Verified omission",
        deadline,
    )
    assert status.excluded_at is None and status.locked_at is not None
    corrected = list(reversed(team_ids))
    correct_prediction(  # type: ignore[arg-type,index]
        session,
        players[0].id,
        player.id,
        season,
        corrected,
        "Corrected transcription",
        deadline,
    )
    assert [item.team_id for item in get_draft(session, player.id, season.id)] == corrected  # type: ignore[attr-defined]
    types = session.scalars(
        select(PredictionSnapshot.snapshot_type).where(
            PredictionSnapshot.player_id == player.id
        )
    ).all()
    assert types == ["admin_reinstatement", "pre_admin_correction", "admin_correction"]


def test_swap_reversal_is_once_only_and_standings_correction_is_versioned() -> None:
    session, season, players = database()
    before = datetime(2026, 8, 20, tzinfo=LONDON)
    player = players[1]  # type: ignore[index]
    original = ensure_draft(session, player.id, season, before)  # type: ignore[arg-type]
    submit_prediction(session, player.id, season, before)  # type: ignore[arg-type]
    process_deadline(session, season, datetime(2026, 8, 21, tzinfo=LONDON))  # type: ignore[arg-type]
    swap = apply_swap(  # type: ignore[arg-type]
        session,
        player.id,
        season,
        original[0].team_id,
        original[-1].team_id,
        datetime(2026, 9, 1, tzinfo=LONDON),
    )
    reverse_swap(  # type: ignore[index]
        session,
        players[0].id,
        swap.id,
        "Wrong teams selected",
        datetime(2026, 9, 2, tzinfo=LONDON),
    )
    assert [item.team_id for item in get_draft(session, player.id, season.id)] == [  # type: ignore[attr-defined]
        item.team_id for item in original
    ]
    with pytest.raises(InvalidAdminAction, match="already been corrected"):
        reverse_swap(  # type: ignore[index]
            session,
            players[0].id,
            swap.id,
            "Second correction",
            datetime(2026, 9, 3, tzinfo=LONDON),
        )
    snapshot = get_latest_snapshot(session, season.id)  # type: ignore[attr-defined]
    assert snapshot is not None
    corrected = correct_standings(  # type: ignore[index]
        session,
        players[0].id,
        snapshot,
        list(reversed([row.team_id for row in snapshot.rows])),
        "Official correction",
        datetime(2026, 9, 3, tzinfo=LONDON),
        is_final=True,
    )
    assert corrected.version == snapshot.version + 1 and corrected.is_final


def test_reason_is_required() -> None:
    with pytest.raises(InvalidAdminAction):
        require_reason("short")
