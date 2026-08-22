import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.service import audit
from app.bulletins import BULLETIN_TITLE
from app.bulletins.fact_pack import FactPack
from app.db.models import Bulletin, BulletinMatch, FootballResult, Season


class InvalidBulletin(ValueError):
    pass


def validate_body(value: str) -> str:
    body = " ".join(value.split())
    words = re.findall(r"\S+", body)
    if len(words) < 15:
        raise InvalidBulletin("The bulletin must contain at least 15 words.")
    if len(words) > 120:
        raise InvalidBulletin("The bulletin must contain no more than 120 words.")
    return body


def _slug(pack: FactPack, timezone: str) -> str:
    period_end = datetime.fromisoformat(pack.period_end.replace("Z", "+00:00"))
    return f"monday-morning-banter-{period_end.astimezone(ZoneInfo(timezone)):%Y-%m-%d}"


def save_draft(
    session: Session,
    pack: FactPack,
    body: str,
    actor_player_id: int,
    now: datetime,
) -> Bulletin:
    normalized_body = validate_body(body)
    period_start = datetime.fromisoformat(pack.period_start.replace("Z", "+00:00"))
    period_end = datetime.fromisoformat(pack.period_end.replace("Z", "+00:00"))
    if pack.season_id <= 0:
        raise InvalidBulletin("The bulletin fact pack has no season.")
    existing = session.scalar(
        select(Bulletin).where(
            Bulletin.season_id == pack.season_id,
            Bulletin.period_end == period_end,
        )
    )
    if existing is not None:
        raise InvalidBulletin("A bulletin already exists for this reporting period.")
    season = session.get(Season, pack.season_id)
    if season is None:
        raise InvalidBulletin("The bulletin season was not found.")
    result_ids = {item.result_id for item in pack.matches}
    stored_ids = set(
        session.scalars(select(FootballResult.id).where(FootballResult.id.in_(result_ids)))
    )
    if stored_ids != result_ids:
        raise InvalidBulletin("The bulletin references unavailable match results.")
    bulletin = Bulletin(
        season_id=pack.season_id,
        slug=_slug(pack, season.timezone),
        title=BULLETIN_TITLE,
        body=normalized_body,
        status="draft",
        period_start=period_start,
        period_end=period_end,
        fact_pack=pack.to_dict(),
        created_by_player_id=actor_player_id,
        created_at=now,
        updated_at=now,
    )
    bulletin.matches = [BulletinMatch(football_result_id=result_id) for result_id in result_ids]
    session.add(bulletin)
    session.flush()
    audit(
        session,
        "bulletin_draft_created",
        now,
        actor_player_id,
        {"bulletin_id": bulletin.id, "period_end": pack.period_end},
    )
    session.commit()
    return bulletin


def update_bulletin(
    session: Session,
    bulletin: Bulletin,
    body: str,
    actor_player_id: int,
    now: datetime,
) -> Bulletin:
    bulletin.body = validate_body(body)
    bulletin.updated_at = now
    audit(
        session,
        "bulletin_updated",
        now,
        actor_player_id,
        {"bulletin_id": bulletin.id, "status": bulletin.status},
    )
    session.commit()
    return bulletin


def save_and_publish_automated(
    session: Session,
    pack: FactPack,
    body: str,
    actor_player_id: int,
    now: datetime,
) -> tuple[Bulletin, bool]:
    """Publish idempotently, while never undoing a human suppression."""
    period_end = datetime.fromisoformat(pack.period_end.replace("Z", "+00:00"))
    existing = session.scalar(
        select(Bulletin).where(
            Bulletin.season_id == pack.season_id,
            Bulletin.period_end == period_end,
        )
    )
    if existing is not None:
        if existing.status == "published":
            return existing, False
        if existing.status == "suppressed":
            raise InvalidBulletin("A suppressed bulletin requires manual administrator review.")
        stored_pack = json.dumps(existing.fact_pack, sort_keys=True, separators=(",", ":"))
        prepared_pack = json.dumps(pack.to_dict(), sort_keys=True, separators=(",", ":"))
        if stored_pack != prepared_pack:
            raise InvalidBulletin("The existing draft uses a different fact pack.")
        update_bulletin(session, existing, body, actor_player_id, now)
        publish_bulletin(session, existing, actor_player_id, now)
        return existing, True
    bulletin = save_draft(session, pack, body, actor_player_id, now)
    publish_bulletin(session, bulletin, actor_player_id, now)
    return bulletin, True


def publish_bulletin(
    session: Session,
    bulletin: Bulletin,
    actor_player_id: int,
    now: datetime,
) -> Bulletin:
    if bulletin.status not in {"draft", "suppressed"}:
        raise InvalidBulletin("Only a draft or suppressed bulletin can be published.")
    validate_body(bulletin.body)
    bulletin.status = "published"
    bulletin.published_at = now
    bulletin.published_by_player_id = actor_player_id
    bulletin.suppressed_at = None
    bulletin.updated_at = now
    audit(
        session,
        "bulletin_published",
        now,
        actor_player_id,
        {"bulletin_id": bulletin.id, "slug": bulletin.slug},
    )
    session.commit()
    return bulletin


def suppress_bulletin(
    session: Session,
    bulletin: Bulletin,
    actor_player_id: int,
    now: datetime,
) -> Bulletin:
    if bulletin.status != "published":
        raise InvalidBulletin("Only a published bulletin can be suppressed.")
    bulletin.status = "suppressed"
    bulletin.suppressed_at = now
    bulletin.updated_at = now
    audit(
        session,
        "bulletin_suppressed",
        now,
        actor_player_id,
        {"bulletin_id": bulletin.id, "slug": bulletin.slug},
    )
    session.commit()
    return bulletin


def get_published_bulletins(session: Session, season_id: int) -> list[Bulletin]:
    return list(
        session.scalars(
            select(Bulletin)
            .options(selectinload(Bulletin.matches))
            .where(Bulletin.season_id == season_id, Bulletin.status == "published")
            .order_by(Bulletin.period_end.desc())
        )
    )
