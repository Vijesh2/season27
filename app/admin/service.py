import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.service import audit, hasher
from app.db.models import (
    AppSession,
    LoginThrottle,
    Player,
    PredictionSnapshot,
    PredictionStatus,
    Season,
    StandingsSnapshot,
    Swap,
)
from app.predictions.service import (
    _replace_order,
    _snapshot_data,
    get_draft,
    get_status,
    validate_order,
)
from app.seasons import london
from app.standings.service import StandingInput, create_snapshot

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class InvalidAdminAction(ValueError):
    pass


def require_reason(value: str) -> str:
    reason = value.strip()
    if len(reason) < 8:
        raise InvalidAdminAction("A reason of at least 8 characters is required.")
    return reason[:500]


def generate_login_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))


def update_player(
    session: Session,
    actor_id: int,
    player_id: int,
    display_name: str,
    is_active: bool,
    now: datetime,
) -> None:
    player = session.get(Player, player_id)
    name = display_name.strip()
    if player is None:
        raise InvalidAdminAction("Player not found.")
    if not name or len(name) > 80:
        raise InvalidAdminAction("Enter a player name of no more than 80 characters.")
    duplicate = session.scalar(
        select(Player.id).where(Player.display_name == name, Player.id != player_id)
    )
    if duplicate is not None:
        raise InvalidAdminAction("That player name is already in use.")
    if player.id == actor_id and not is_active:
        raise InvalidAdminAction("You cannot deactivate your own administrator account.")
    player.display_name = name
    player.is_active = is_active
    player.updated_at = now
    if not is_active:
        for app_session in session.scalars(
            select(AppSession).where(
                AppSession.player_id == player_id, AppSession.revoked_at.is_(None)
            )
        ):
            app_session.revoked_at = now
    audit(
        session,
        "player_updated",
        now,
        actor_id,
        {"target_player_id": player_id, "is_active": is_active},
    )
    session.commit()


def rotate_login_code(
    session: Session, actor_id: int, player_id: int, now: datetime
) -> str:
    player = session.get(Player, player_id)
    if player is None or not player.is_active:
        raise InvalidAdminAction("Player not found.")
    code = generate_login_code()
    player.login_code_hash = hasher.hash(code)
    player.updated_at = now
    for app_session in session.scalars(
        select(AppSession).where(
            AppSession.player_id == player_id,
            AppSession.revoked_at.is_(None),
        )
    ):
        app_session.revoked_at = now
    audit(
        session,
        "login_code_rotated",
        now,
        actor_id,
        {"target_player_id": player_id},
    )
    session.commit()
    return code


def reset_player_lock(session: Session, actor_id: int, player_id: int, now: datetime) -> None:
    player = session.get(Player, player_id)
    if player is None:
        raise InvalidAdminAction("Player not found.")
    player.failed_login_count = 0
    player.locked_until = None
    player.updated_at = now
    throttles = list(
        session.scalars(select(LoginThrottle).where(LoginThrottle.locked_until.is_not(None)))
    )
    for throttle in throttles:
        throttle.failed_count = 0
        throttle.locked_until = None
    audit(
        session,
        "player_login_unlocked",
        now,
        actor_id,
        {"target_player_id": player_id, "cleared_throttles": len(throttles)},
    )
    session.commit()


def revoke_session(
    session: Session, actor_id: int, session_id: int, now: datetime
) -> AppSession:
    target = session.get(AppSession, session_id)
    if target is None:
        raise InvalidAdminAction("Session not found.")
    if target.revoked_at is None:
        target.revoked_at = now
        audit(
            session,
            "session_revoked",
            now,
            actor_id,
            {"target_player_id": target.player_id, "session_id": target.id},
        )
        session.commit()
    return target


def revoke_player_sessions(
    session: Session, actor_id: int, player_id: int, now: datetime
) -> int:
    targets = list(
        session.scalars(
            select(AppSession).where(
                AppSession.player_id == player_id,
                AppSession.revoked_at.is_(None),
            )
        )
    )
    for target in targets:
        target.revoked_at = now
    audit(
        session,
        "all_sessions_revoked",
        now,
        actor_id,
        {"target_player_id": player_id, "count": len(targets)},
    )
    session.commit()
    return len(targets)


def update_season_dates(
    session: Session,
    actor_id: int,
    season: Season,
    opens_at: datetime,
    locks_at: datetime,
    now: datetime,
) -> None:
    if now >= london(season.prediction_locks_at):
        raise InvalidAdminAction("Season dates cannot be edited after predictions lock.")
    if opens_at >= locks_at:
        raise InvalidAdminAction("The game must open before predictions lock.")
    locked_status = session.scalar(
        select(PredictionStatus.id).where(
            PredictionStatus.season_id == season.id,
            PredictionStatus.locked_at.is_not(None),
        )
    )
    if locked_status is not None:
        raise InvalidAdminAction("Season dates cannot be edited after a prediction is locked.")
    season.game_opens_at = opens_at
    season.prediction_locks_at = locks_at
    if season.swap_windows:
        season.swap_windows[0].opens_at = locks_at
    audit(session, "season_dates_updated", now, actor_id, {"season_id": season.id})
    session.commit()


def reinstate_player(
    session: Session,
    actor_id: int,
    player_id: int,
    season: Season,
    team_ids: list[int],
    reason: str,
    now: datetime,
) -> None:
    reason = require_reason(reason)
    status = get_status(session, player_id, season.id)
    if status is None or status.excluded_at is None:
        raise InvalidAdminAction("Only an excluded player can be reinstated.")
    validate_order(session, season.id, team_ids)
    _replace_order(session, player_id, season.id, team_ids, now)
    status.excluded_at = None
    status.locked_at = now
    status.submitted_at = now
    status.submitted_order = team_ids
    session.add(
        PredictionSnapshot(
            player_id=player_id,
            season_id=season.id,
            snapshot_type="admin_reinstatement",
            prediction_data=_snapshot_data(team_ids),
            created_at=now,
        )
    )
    audit(
        session,
        "player_reinstated",
        now,
        actor_id,
        {"target_player_id": player_id, "season_id": season.id, "reason": reason},
    )
    session.commit()


def correct_prediction(
    session: Session,
    actor_id: int,
    player_id: int,
    season: Season,
    team_ids: list[int],
    reason: str,
    now: datetime,
) -> None:
    reason = require_reason(reason)
    status = get_status(session, player_id, season.id)
    if status is None or status.locked_at is None or status.excluded_at is not None:
        raise InvalidAdminAction("A locked eligible prediction is required.")
    validate_order(session, season.id, team_ids)
    before = [item.team_id for item in get_draft(session, player_id, season.id)]
    _replace_order(session, player_id, season.id, team_ids, now)
    session.add_all(
        (
            PredictionSnapshot(
                player_id=player_id,
                season_id=season.id,
                snapshot_type="pre_admin_correction",
                prediction_data=_snapshot_data(before),
                created_at=now,
            ),
            PredictionSnapshot(
                player_id=player_id,
                season_id=season.id,
                snapshot_type="admin_correction",
                prediction_data=_snapshot_data(team_ids),
                created_at=now,
            ),
        )
    )
    audit(
        session,
        "prediction_corrected",
        now,
        actor_id,
        {"target_player_id": player_id, "season_id": season.id, "reason": reason},
    )
    session.commit()


def reverse_swap(
    session: Session, actor_id: int, swap_id: int, reason: str, now: datetime
) -> None:
    reason = require_reason(reason)
    swap = session.get(Swap, swap_id)
    if swap is None:
        raise InvalidAdminAction("Swap not found.")
    if swap.corrected_at is not None:
        raise InvalidAdminAction("This swap has already been corrected.")
    before = [item.team_id for item in get_draft(session, swap.player_id, swap.season_id)]
    if swap.first_team_id not in before or swap.second_team_id not in before:
        raise InvalidAdminAction("The recorded teams are no longer in the prediction.")
    after = list(before)
    first_index = after.index(swap.first_team_id)
    second_index = after.index(swap.second_team_id)
    after[first_index], after[second_index] = after[second_index], after[first_index]
    _replace_order(session, swap.player_id, swap.season_id, after, now)
    swap.corrected_at = now
    swap.correction_reason = reason
    swap.corrected_by_player_id = actor_id
    session.add_all(
        (
            PredictionSnapshot(
                player_id=swap.player_id,
                season_id=swap.season_id,
                snapshot_type="pre_admin_correction",
                prediction_data=_snapshot_data(before),
                created_at=now,
            ),
            PredictionSnapshot(
                player_id=swap.player_id,
                season_id=swap.season_id,
                snapshot_type="admin_correction",
                prediction_data=_snapshot_data(after),
                created_at=now,
            ),
        )
    )
    audit(
        session,
        "swap_corrected",
        now,
        actor_id,
        {"swap_id": swap.id, "target_player_id": swap.player_id, "reason": reason},
    )
    session.commit()


def correct_standings(
    session: Session,
    actor_id: int,
    snapshot: StandingsSnapshot,
    team_ids: list[int],
    reason: str,
    now: datetime,
    *,
    is_final: bool,
) -> StandingsSnapshot:
    reason = require_reason(reason)
    existing = {row.team_id: row for row in snapshot.rows}
    corrected = create_snapshot(
        session,
        snapshot.season_id,
        [
            StandingInput(
                team_id=team_id,
                position=position,
                played=existing[team_id].played,
                points=existing[team_id].points,
                goal_difference=existing[team_id].goal_difference,
            )
            for position, team_id in enumerate(team_ids, start=1)
        ],
        now,
        source="admin",
        is_final=is_final,
    )
    audit(
        session,
        "standings_corrected",
        now,
        actor_id,
        {"season_id": snapshot.season_id, "version": corrected.version, "reason": reason},
    )
    session.commit()
    return corrected
