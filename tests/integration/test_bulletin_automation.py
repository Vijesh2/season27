from datetime import datetime

from starlette.testclient import TestClient

from app.clock import LONDON, MutableClock
from app.config import Settings
from app.main import create_app
from app.results.source import ExternalResult
from app.seasons import get_current_season
from app.standings.service import StandingInput, create_snapshot
from app.standings.source import ExternalStanding, SourceTable
from app.teams.service import get_season_teams

TOKEN = "automation-test-token-that-is-long-enough"
BODY = (
    "No goals disturbed the furniture this week, so the leaderboard remains gloriously "
    "unchanged and every tactical genius may keep claiming the masterplan is working."
)


class EmptyResults:
    def fetch(
        self, period_start: datetime, period_end: datetime
    ) -> tuple[ExternalResult, ...]:
        return ()


class FixedStandings:
    name = "test"

    def __init__(self) -> None:
        self.table: SourceTable | None = None

    def fetch(self) -> SourceTable:
        assert self.table is not None
        return self.table


def test_automation_prepares_publishes_retries_and_verifies(database_url: str) -> None:
    source = FixedStandings()
    clock = MutableClock(datetime(2026, 8, 24, 6, 5, tzinfo=LONDON))
    settings = Settings(
        database_url=database_url,
        environment="test",
        bulletin_automation_token=TOKEN,
        bulletin_automation_actor_name="Administrator",
    )
    app = create_app(settings, clock, source, EmptyResults())
    with app.state.session_factory() as session:
        season = get_current_season(session)
        assert season is not None
        teams = get_season_teams(session, season.id)
        rows = [
            StandingInput(item.team_id, position, 1, 3, 1, 1)
            for position, item in enumerate(teams, start=1)
        ]
        create_snapshot(
            session,
            season.id,
            rows,
            datetime(2026, 8, 17, 6, tzinfo=LONDON),
            source="test",
        )
        source.table = SourceTable(
            tuple(
                ExternalStanding(
                    identity=item.team.source_identity,
                    name=item.team.name,
                    position=position,
                    played=1,
                    points=3,
                    goal_difference=1,
                    goals_scored=1,
                )
                for position, item in enumerate(teams, start=1)
            ),
            False,
        )

    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        assert client.post("/internal/bulletins/prepare", json={}).status_code == 403
        prepared_response = client.post(
            "/internal/bulletins/prepare", headers=headers, json={}
        )
        assert prepared_response.status_code == 200
        prepared = prepared_response.json()
        assert prepared["status"] == "prepared"
        assert prepared["fact_pack"]["baseline_snapshot"] == prepared["fact_pack"][
            "current_snapshot"
        ]

        payload = {
            "period_start": prepared["fact_pack"]["period_start"],
            "period_end": prepared["fact_pack"]["period_end"],
            "fact_pack_digest": prepared["fact_pack_digest"],
            "body": BODY,
        }
        published = client.post(
            "/internal/bulletins/publish", headers=headers, json=payload
        )
        assert published.status_code == 200
        assert published.json()["status"] == "published"
        slug = published.json()["slug"]

        retry = client.post("/internal/bulletins/publish", headers=headers, json=payload)
        assert retry.status_code == 200
        assert retry.json()["status"] == "already_published"
        verified = client.get(f"/internal/bulletins/{slug}", headers=headers)
        assert verified.json()["status"] == "published"
        assert verified.json()["body"] == BODY


def test_automation_rejects_changed_fact_digest(database_url: str) -> None:
    source = FixedStandings()
    settings = Settings(
        database_url=database_url,
        environment="test",
        dev_now="2026-08-24T06:05:00+01:00",
        bulletin_automation_token=TOKEN,
        bulletin_automation_actor_name="Administrator",
    )
    app = create_app(settings, standings_source=source, results_source=EmptyResults())
    with app.state.session_factory() as session:
        season = get_current_season(session)
        assert season is not None
        teams = get_season_teams(session, season.id)
        rows = [StandingInput(item.team_id, i, 1, 3, 1, 1) for i, item in enumerate(teams, 1)]
        create_snapshot(
            session, season.id, rows, datetime(2026, 8, 17, 6, tzinfo=LONDON), source="test"
        )
        source.table = SourceTable(
            tuple(
                ExternalStanding(
                    item.team.source_identity, item.team.name, i, 1, 3, 1, 1
                )
                for i, item in enumerate(teams, 1)
            ),
            False,
        )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        prepared = client.post(
            "/internal/bulletins/prepare", headers=headers, json={}
        ).json()
        response = client.post(
            "/internal/bulletins/publish",
            headers=headers,
            json={
                "period_start": prepared["fact_pack"]["period_start"],
                "period_end": prepared["fact_pack"]["period_end"],
                "fact_pack_digest": "0" * 64,
                "body": BODY,
            },
        )
        assert response.status_code == 409
        assert response.json()["status"] == "facts_changed"
