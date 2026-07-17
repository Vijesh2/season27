import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.db.models import AppSession, AuditEvent, LoginThrottle, Player
from app.seasons import london

CODE_PATTERN = re.compile(r"^[A-Z0-9]{4}$")
GENERIC_LOGIN_ERROR = "We couldn't sign you in. Check the code and try again."
SESSION_COOKIE = "season27_session"
LOGIN_CSRF_COOKIE = "season27_login_csrf"
hasher = PasswordHasher()


@dataclass(frozen=True)
class LoginResult:
    player: Player | None
    token: str | None = None
    csrf_token: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class DevelopmentPlayer:
    display_name: str
    code: str
    is_admin: bool = False


def development_player_seeds() -> list[DevelopmentPlayer]:
    return [
        DevelopmentPlayer(
            display_name="Administrator" if index == 1 else f"Player {index}",
            code=f"D{index:03}",
            is_admin=index == 1,
        )
        for index in range(1, 6)
    ]


def normalize_code(value: str) -> str | None:
    normalized = value.strip().upper()
    return normalized if CODE_PATTERN.fullmatch(normalized) else None


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def throttle_key(settings: Settings, kind: str, value: str) -> str:
    message = f"{kind}:{value}".encode()
    return hmac.new(settings.secret_key.encode(), message, hashlib.sha256).hexdigest()


def audit(
    session: Session,
    event_type: str,
    now: datetime,
    player_id: int | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor_player_id=player_id,
            event_type=event_type,
            event_metadata=metadata or {},
            created_at=now,
            retention_until=now + timedelta(days=3 * 365),
        )
    )


def seed_development_players(
    session: Session,
    now: datetime,
    seeds: list[DevelopmentPlayer] | None = None,
) -> list[Player]:
    seeds = seeds or development_player_seeds()
    players: list[Player] = []
    for seed in seeds:
        existing = session.scalar(select(Player).where(Player.display_name == seed.display_name))
        if existing is not None:
            players.append(existing)
            continue
        player = Player(
            display_name=seed.display_name,
            login_code_hash=hasher.hash(seed.code),
            is_admin=seed.is_admin,
            is_active=True,
            failed_login_count=0,
            created_at=now,
            updated_at=now,
        )
        session.add(player)
        players.append(player)
    session.commit()
    return players


def bootstrap_admin(session: Session, name: str, code: str, now: datetime) -> Player:
    """Create the first administrator without ever persisting the clear-text code."""
    normalized = normalize_code(code)
    if normalized is None:
        raise ValueError("bootstrap administrator code must be four letters or digits")
    existing = session.scalar(select(Player).order_by(Player.id).limit(1))
    if existing is not None:
        return existing
    player = Player(
        display_name=name.strip(),
        login_code_hash=hasher.hash(normalized),
        is_admin=True,
        is_active=True,
        failed_login_count=0,
        created_at=now,
        updated_at=now,
    )
    session.add(player)
    session.commit()
    return player


def _throttle(session: Session, key_hash: str, now: datetime) -> LoginThrottle:
    throttle = session.scalar(select(LoginThrottle).where(LoginThrottle.key_hash == key_hash))
    if throttle is None:
        throttle = LoginThrottle(key_hash=key_hash, failed_count=0, window_started_at=now)
        session.add(throttle)
        session.flush()
    if throttle.locked_until and now >= london(throttle.locked_until):
        throttle.failed_count = 0
        throttle.locked_until = None
        throttle.window_started_at = now
    return throttle


def _is_locked(throttles: list[LoginThrottle], now: datetime) -> bool:
    return any(item.locked_until and now < london(item.locked_until) for item in throttles)


def _record_failure(
    session: Session, throttles: list[LoginThrottle], now: datetime, settings: Settings
) -> None:
    for throttle in throttles:
        throttle.failed_count += 1
        if throttle.failed_count >= settings.login_attempt_limit:
            throttle.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
    audit(session, "login_failed", now)
    session.commit()


def authenticate(
    session: Session, raw_code: str, ip: str, now: datetime, settings: Settings
) -> LoginResult:
    normalized = normalize_code(raw_code)
    code_key = throttle_key(settings, "code", normalized or raw_code.strip().upper())
    ip_key = throttle_key(settings, "ip", ip)
    throttles = [_throttle(session, code_key, now), _throttle(session, ip_key, now)]
    if _is_locked(throttles, now):
        audit(session, "login_failed", now)
        session.commit()
        return LoginResult(None, error=GENERIC_LOGIN_ERROR)

    matched: Player | None = None
    if normalized is not None:
        for player in session.scalars(select(Player).where(Player.is_active.is_(True))):
            try:
                if hasher.verify(player.login_code_hash, normalized):
                    matched = player
                    break
            except VerifyMismatchError:
                continue

    if matched is None:
        _record_failure(session, throttles, now, settings)
        return LoginResult(None, error=GENERIC_LOGIN_ERROR)

    matched.failed_login_count = 0
    matched.locked_until = None
    matched.updated_at = now
    for throttle in throttles:
        throttle.failed_count = 0
        throttle.locked_until = None
    raw_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(24)
    app_session = AppSession(
        player_id=matched.id,
        token_hash=digest(raw_token),
        csrf_token=csrf_token,
        expires_at=now + timedelta(days=settings.session_days),
        created_at=now,
        last_seen_at=now,
    )
    session.add(app_session)
    audit(session, "login_succeeded", now, matched.id)
    session.commit()
    return LoginResult(matched, raw_token, csrf_token)


def resolve_session(session: Session, raw_token: str | None, now: datetime) -> AppSession | None:
    if not raw_token:
        return None
    app_session = session.scalar(
        select(AppSession)
        .options(selectinload(AppSession.player))
        .where(AppSession.token_hash == digest(raw_token))
    )
    if app_session is None or app_session.revoked_at is not None:
        return None
    if now >= london(app_session.expires_at):
        return None
    app_session.last_seen_at = now
    session.commit()
    return app_session


def logout(session: Session, app_session: AppSession, now: datetime) -> None:
    app_session.revoked_at = now
    audit(session, "logout", now, app_session.player_id)
    session.commit()
