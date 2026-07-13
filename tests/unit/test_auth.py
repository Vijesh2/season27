from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.auth.service import (
    GENERIC_LOGIN_ERROR,
    authenticate,
    development_player_seeds,
    digest,
    logout,
    normalize_code,
    resolve_session,
    seed_development_players,
)
from app.clock import LONDON
from app.config import Settings
from app.db.models import AppSession, AuditEvent, Base, LoginThrottle, Player

ADMIN = development_player_seeds()[0]
REGULAR = development_player_seeds()[1]


@pytest.fixture
def auth_session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    seed_development_players(session, datetime(2026, 7, 12, tzinfo=LONDON))
    return session


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(f" {ADMIN.code.lower()} ", ADMIN.code), ("abc", None), ("ABCDE", None), ("A-12", None)],
)
def test_normalize_code(raw: str, expected: str | None) -> None:
    assert normalize_code(raw) == expected


def test_codes_and_session_tokens_are_hashed(auth_session: Session) -> None:
    settings = Settings()
    now = datetime(2026, 7, 12, tzinfo=LONDON)
    result = authenticate(auth_session, ADMIN.code, "127.0.0.1", now, settings)
    player = auth_session.scalar(select(Player).where(Player.display_name == ADMIN.display_name))
    stored = auth_session.scalar(select(AppSession))
    assert player is not None and player.login_code_hash != ADMIN.code
    assert stored is not None and result.token is not None
    assert stored.token_hash == digest(result.token)
    assert result.token not in stored.token_hash


def test_failed_login_is_generic_and_audited_without_sensitive_data(auth_session: Session) -> None:
    result = authenticate(
        auth_session, "bad!", "192.0.2.10", datetime(2026, 7, 12, tzinfo=LONDON), Settings()
    )
    event = auth_session.scalar(select(AuditEvent).where(AuditEvent.event_type == "login_failed"))
    assert result.error == GENERIC_LOGIN_ERROR
    assert event is not None and event.event_metadata == {}


def test_five_failures_lock_code_and_ip_then_lock_expires(auth_session: Session) -> None:
    settings = Settings()
    now = datetime(2026, 7, 12, tzinfo=LONDON)
    for _ in range(5):
        authenticate(auth_session, "NOPE", "192.0.2.11", now, settings)
    throttles = auth_session.scalars(select(LoginThrottle)).all()
    assert len(throttles) == 2
    assert all(item.locked_until is not None for item in throttles)
    assert authenticate(auth_session, ADMIN.code, "192.0.2.11", now, settings).player is None
    later = now + timedelta(minutes=16)
    assert authenticate(auth_session, ADMIN.code, "192.0.2.11", later, settings).player is not None


def test_session_expiry_and_logout(auth_session: Session) -> None:
    settings = Settings(session_days=1)
    now = datetime(2026, 7, 12, tzinfo=LONDON)
    result = authenticate(auth_session, ADMIN.code, "127.0.0.1", now, settings)
    assert result.token is not None
    active = resolve_session(auth_session, result.token, now)
    assert active is not None
    assert resolve_session(auth_session, result.token, now + timedelta(days=1)) is None
    logout(auth_session, active, now)
    assert resolve_session(auth_session, result.token, now) is None


def test_multiple_sessions_are_independent(auth_session: Session) -> None:
    now = datetime(2026, 7, 12, tzinfo=LONDON)
    first = authenticate(auth_session, REGULAR.code, "127.0.0.1", now, Settings())
    second = authenticate(auth_session, REGULAR.code, "127.0.0.2", now, Settings())
    assert first.token and second.token and first.token != second.token
    first_session = resolve_session(auth_session, first.token, now)
    assert first_session is not None
    logout(auth_session, first_session, now)
    assert resolve_session(auth_session, first.token, now) is None
    assert resolve_session(auth_session, second.token, now) is not None


def test_seed_is_idempotent(auth_session: Session) -> None:
    players = seed_development_players(auth_session, datetime(2026, 7, 12, tzinfo=LONDON))
    assert len(players) == 5
    assert len(auth_session.scalars(select(Player)).all()) == 5
