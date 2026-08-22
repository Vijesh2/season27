from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Season(Base):
    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(20), unique=True)
    timezone: Mapped[str] = mapped_column(String(50))
    game_opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prediction_locks_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="scheduled")
    swap_windows: Mapped[list["SwapWindow"]] = relationship(
        back_populates="season", order_by="SwapWindow.sequence_number"
    )


class SwapWindow(Base):
    __tablename__ = "swap_windows"
    __table_args__ = (UniqueConstraint("season_id", "sequence_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"))
    sequence_number: Mapped[int] = mapped_column(Integer)
    opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    season: Mapped[Season] = relationship(back_populates="swap_windows")


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80), unique=True)
    login_code_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AppSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    player: Mapped[Player] = relationship(foreign_keys=[player_id])


class LoginThrottle(Base):
    __tablename__ = "login_throttles"

    id: Mapped[int] = mapped_column(primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    event_metadata: Mapped[dict[str, object]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    retention_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    short_name: Mapped[str] = mapped_column(String(30))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    source_identity: Mapped[str] = mapped_column(String(100), unique=True)
    badge_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)


class FootballResult(Base):
    __tablename__ = "football_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_event_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    competition: Mapped[str] = mapped_column(String(100))
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"))
    home_score: Mapped[int] = mapped_column(Integer)
    away_score: Mapped[int] = mapped_column(Integer)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_status: Mapped[str] = mapped_column(String(30))
    source_url: Mapped[str] = mapped_column(String(500))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])


class Bulletin(Base):
    __tablename__ = "bulletins"
    __table_args__ = (
        UniqueConstraint("season_id", "period_end"),
        CheckConstraint(
            "status IN ('draft', 'published', 'suppressed')",
            name="ck_bulletins_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fact_pack: Mapped[dict[str, object]] = mapped_column(JSON)
    created_by_player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="RESTRICT")
    )
    published_by_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suppressed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    season: Mapped[Season] = relationship()
    created_by: Mapped[Player] = relationship(foreign_keys=[created_by_player_id])
    published_by: Mapped[Player | None] = relationship(foreign_keys=[published_by_player_id])
    matches: Mapped[list["BulletinMatch"]] = relationship(
        back_populates="bulletin", cascade="all, delete-orphan"
    )


class BulletinMatch(Base):
    __tablename__ = "bulletin_matches"
    __table_args__ = (UniqueConstraint("bulletin_id", "football_result_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bulletin_id: Mapped[int] = mapped_column(
        ForeignKey("bulletins.id", ondelete="CASCADE")
    )
    football_result_id: Mapped[int] = mapped_column(
        ForeignKey("football_results.id", ondelete="RESTRICT")
    )
    bulletin: Mapped[Bulletin] = relationship(back_populates="matches")
    football_result: Mapped[FootballResult] = relationship()


class SeasonTeam(Base):
    __tablename__ = "season_teams"
    __table_args__ = (
        UniqueConstraint("season_id", "team_id"),
        UniqueConstraint("season_id", "display_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"))
    display_order: Mapped[int] = mapped_column(Integer)
    season: Mapped[Season] = relationship()
    team: Mapped[Team] = relationship()


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("player_id", "season_id", "team_id"),
        UniqueConstraint("player_id", "season_id", "predicted_position"),
        CheckConstraint(
            "predicted_position >= 1 AND predicted_position <= 20",
            name="ck_predictions_position",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"))
    predicted_position: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    team: Mapped[Team] = relationship()


class PredictionStatus(Base):
    __tablename__ = "prediction_statuses"
    __table_args__ = (UniqueConstraint("player_id", "season_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_order: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    excluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PredictionSnapshot(Base):
    __tablename__ = "prediction_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"))
    snapshot_type: Mapped[str] = mapped_column(String(30))
    prediction_data: Mapped[list[dict[str, int]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Swap(Base):
    __tablename__ = "swaps"
    __table_args__ = (UniqueConstraint("player_id", "season_id", "swap_window_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"))
    swap_window_id: Mapped[int] = mapped_column(
        ForeignKey("swap_windows.id", ondelete="RESTRICT")
    )
    first_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"))
    second_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"))
    first_position: Mapped[int] = mapped_column(Integer)
    second_position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correction_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    corrected_by_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )
    swap_window: Mapped[SwapWindow] = relationship()
    player: Mapped[Player] = relationship(foreign_keys=[player_id])
    corrected_by: Mapped[Player | None] = relationship(foreign_keys=[corrected_by_player_id])
    first_team: Mapped[Team] = relationship(foreign_keys=[first_team_id])
    second_team: Mapped[Team] = relationship(foreign_keys=[second_team_id])


class StandingsSnapshot(Base):
    __tablename__ = "standings_snapshots"
    __table_args__ = (UniqueConstraint("season_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(30))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    rows: Mapped[list["Standing"]] = relationship(
        back_populates="snapshot",
        order_by="Standing.position",
        cascade="all, delete-orphan",
    )


class Standing(Base):
    __tablename__ = "standings"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "team_id"),
        UniqueConstraint("snapshot_id", "position"),
        CheckConstraint("position >= 1 AND position <= 20", name="ck_standings_position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("standings_snapshots.id", ondelete="CASCADE")
    )
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"))
    position: Mapped[int] = mapped_column(Integer)
    played: Mapped[int | None] = mapped_column(Integer, nullable=True)
    points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goal_difference: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goals_scored: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot: Mapped[StandingsSnapshot] = relationship(back_populates="rows")
    team: Mapped[Team] = relationship()


class StandingsRefreshState(Base):
    __tablename__ = "standings_refresh_states"
    __table_args__ = (UniqueConstraint("season_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    incident_open: Mapped[bool] = mapped_column(Boolean, default=False)


class StandingsRefreshThrottle(Base):
    __tablename__ = "standings_refresh_throttles"

    id: Mapped[int] = mapped_column(primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
