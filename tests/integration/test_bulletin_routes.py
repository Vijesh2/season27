import re
from datetime import datetime

from sqlalchemy import select
from starlette.testclient import TestClient

from app.auth.service import development_player_seeds
from app.clock import LONDON
from app.config import Settings
from app.db.models import Bulletin
from app.main import create_app
from app.seasons import get_current_season
from app.standings.service import StandingInput, create_snapshot, get_latest_snapshot

BODY = (
    "Arsenal nudged the numbers in Alex’s favour, while Sam’s carefully engineered "
    "masterplan encountered another entirely unforeseen encounter with association football."
)
REVISED_BODY = BODY + " The calculator has declined to comment."


def csrf(page: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)', page)
    assert match
    return match.group(1)


def login(client: TestClient, code: str) -> None:
    page = client.get("/login")
    response = client.post(
        "/login",
        data={"code": code, "csrf_token": csrf(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def add_fact_pack_snapshots(app: object) -> None:
    with app.state.session_factory() as session:  # type: ignore[attr-defined]
        season = get_current_season(session)
        assert season is not None
        current = get_latest_snapshot(session, season.id)
        assert current is not None
        rows = [
            StandingInput(
                team_id=row.team_id,
                position=row.position,
                played=row.played,
                points=row.points,
                goal_difference=row.goal_difference,
                goals_scored=row.goals_scored,
            )
            for row in current.rows
        ]
        create_snapshot(
            session,
            season.id,
            rows,
            datetime(2026, 8, 17, 6, tzinfo=LONDON),
            source="test",
        )
        create_snapshot(
            session,
            season.id,
            rows,
            datetime(2026, 8, 24, 12, tzinfo=LONDON),
            source="test",
        )


def test_admin_preview_publish_suppress_and_republish_workflow(database_url: str) -> None:
    settings = Settings(database_url=database_url, dev_now="2026-08-24T13:00:00+01:00")
    app = create_app(settings)
    add_fact_pack_snapshots(app)
    with TestClient(app) as admin:
        login(admin, development_player_seeds()[0].code)
        listing = admin.get("/admin/bulletins")
        assert listing.status_code == 200
        assert "Nothing is public until Publish is selected" in listing.text

        rejected = admin.post(
            "/admin/bulletins/preview",
            data={
                "csrf_token": "bad",
                "period_start": "2026-08-17T06:00",
                "period_end": "2026-08-24T13:00",
                "body": BODY,
            },
        )
        assert rejected.status_code == 403

        created = admin.post(
            "/admin/bulletins/preview",
            data={
                "csrf_token": csrf(listing.text),
                "period_start": "2026-08-17T06:00",
                "period_end": "2026-08-24T13:00",
                "body": BODY,
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        preview_url = created.headers["location"]
        preview = admin.get(preview_url)
        assert BODY in preview.text
        assert "Status: Draft" in preview.text
        assert "Verified source matches" in preview.text
        assert admin.get("/bulletins").text.find(BODY) == -1

        updated = admin.post(
            f"{preview_url}/update",
            data={"csrf_token": csrf(preview.text), "body": REVISED_BODY},
            follow_redirects=False,
        )
        assert updated.status_code == 303
        preview = admin.get(preview_url)
        assert REVISED_BODY in preview.text

        published = admin.post(
            f"{preview_url}/publish",
            data={"csrf_token": csrf(preview.text)},
            follow_redirects=False,
        )
        assert published.status_code == 303
        archive = admin.get("/bulletins")
        assert REVISED_BODY in archive.text
        with app.state.session_factory() as session:
            bulletin = session.scalar(select(Bulletin))
            assert bulletin is not None
            slug = bulletin.slug
        detail = admin.get(f"/bulletins/{slug}")
        assert detail.status_code == 200 and REVISED_BODY in detail.text
        assert REVISED_BODY in admin.get("/").text

        preview = admin.get(preview_url)
        suppressed = admin.post(
            f"{preview_url}/suppress",
            data={"csrf_token": csrf(preview.text)},
            follow_redirects=False,
        )
        assert suppressed.status_code == 303
        assert admin.get(f"/bulletins/{slug}").status_code == 404
        assert REVISED_BODY not in admin.get("/").text

        preview = admin.get(preview_url)
        assert "Republish" in preview.text
        republished = admin.post(
            f"{preview_url}/publish",
            data={"csrf_token": csrf(preview.text)},
            follow_redirects=False,
        )
        assert republished.status_code == 303
        assert admin.get(f"/bulletins/{slug}").status_code == 200


def test_bulletins_require_login_and_admin_pages_reject_players(database_url: str) -> None:
    settings = Settings(database_url=database_url, dev_now="2026-08-24T13:00:00+01:00")
    with TestClient(create_app(settings)) as client:
        assert client.get("/bulletins", follow_redirects=False).status_code == 303
        login(client, development_player_seeds()[1].code)
        assert client.get("/bulletins").status_code == 200
        assert client.get("/admin/bulletins").status_code == 403
