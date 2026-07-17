import re

from sqlalchemy import select
from starlette.testclient import TestClient

from app.auth.service import development_player_seeds
from app.config import Settings
from app.db.models import AppSession, Player
from app.main import create_app
from app.seasons import get_current_season
from app.teams.service import get_season_teams


def login(client: TestClient, code: str) -> None:
    page = client.get("/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)', page.text)
    assert csrf
    response = client.post(
        "/login",
        data={"code": code, "csrf_token": csrf.group(1)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def csrf(page: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)', page)
    assert match
    return match.group(1)


def submit_prediction(database_url: str, player_index: int) -> None:
    settings = Settings(database_url=database_url, dev_now="2026-08-20T12:00:00")
    with TestClient(create_app(settings)) as client:
        login(client, development_player_seeds()[player_index].code)
        review = client.get("/prediction/review")
        response = client.post(
            "/prediction/submit",
            data={"csrf_token": csrf(review.text), "confirmed": "yes"},
            follow_redirects=False,
        )
        assert response.status_code == 303


def test_admin_routes_require_admin_and_csrf(database_url: str) -> None:
    settings = Settings(database_url=database_url, dev_now="2026-08-02T12:00:00")
    with TestClient(create_app(settings)) as client:
        login(client, development_player_seeds()[1].code)
        for path in ("/admin/players", "/admin/sessions", "/admin/game", "/admin/audit"):
            assert client.get(path).status_code == 403
        client.cookies.clear()
        login(client, development_player_seeds()[0].code)
        rejected = client.post(
            "/admin/players/2/rotate-code", data={"csrf_token": "bad"}
        )
        assert rejected.status_code == 403


def test_player_management_rotates_code_once_and_revokes_sessions(database_url: str) -> None:
    settings = Settings(database_url=database_url, dev_now="2026-08-02T12:00:00")
    app = create_app(settings)
    with TestClient(app) as admin, TestClient(app) as player:
        seeds = development_player_seeds()
        login(player, seeds[1].code)
        login(admin, seeds[0].code)
        page = admin.get("/admin/players")
        token = csrf(page.text)
        updated = admin.post(
            "/admin/players/2/update",
            data={
                "csrf_token": token,
                "display_name": "Configured Player",
                "is_active": "yes",
            },
            follow_redirects=False,
        )
        assert updated.status_code == 303
        rotated = admin.post(
            "/admin/players/2/rotate-code",
            data={"csrf_token": token},
        )
        assert rotated.status_code == 200
        match = re.search(r'class="one-time-code">([A-Z2-9]{4})<', rotated.text)
        assert match
        generated_code = match.group(1)
        assert "will not be shown again" in rotated.text
        assert rotated.headers["cache-control"] == "no-store"
        assert player.get("/", follow_redirects=False).status_code == 303
        player.cookies.clear()
        old_login = player.get("/login")
        old_attempt = player.post(
            "/login",
            data={"code": seeds[1].code, "csrf_token": csrf(old_login.text)},
            follow_redirects=False,
        )
        assert old_attempt.status_code == 200
        new_login = player.get("/login")
        new_attempt = player.post(
            "/login",
            data={"code": generated_code, "csrf_token": csrf(new_login.text)},
            follow_redirects=False,
        )
        assert new_attempt.status_code == 303
        assert generated_code not in admin.get("/admin/players").text


def test_admin_can_revoke_an_individual_device_session(database_url: str) -> None:
    settings = Settings(database_url=database_url, dev_now="2026-08-02T12:00:00")
    app = create_app(settings)
    with TestClient(app) as admin, TestClient(app) as player:
        login(player, development_player_seeds()[1].code)
        login(admin, development_player_seeds()[0].code)
        with app.state.session_factory() as session:
            target_player = session.scalar(select(Player).where(Player.display_name == "Player 2"))
            assert target_player is not None
            target_session = session.scalar(
                select(AppSession).where(
                    AppSession.player_id == target_player.id,
                    AppSession.revoked_at.is_(None),
                )
            )
            assert target_session is not None
            session_id = target_session.id
        page = admin.get("/admin/sessions")
        response = admin.post(
            f"/admin/sessions/{session_id}/revoke",
            data={"csrf_token": csrf(page.text)},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert player.get("/", follow_redirects=False).headers["location"] == "/login"


def test_season_dates_update_before_lock_and_reject_after_lock(database_url: str) -> None:
    early = Settings(database_url=database_url, dev_now="2026-08-02T12:00:00")
    with TestClient(create_app(early)) as client:
        login(client, development_player_seeds()[0].code)
        page = client.get("/admin/season")
        response = client.post(
            "/admin/season",
            data={
                "csrf_token": csrf(page.text),
                "game_opens_at": "2026-08-03T00:00",
                "prediction_locks_at": "2026-08-22T00:00",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
    late = Settings(database_url=database_url, dev_now="2026-08-22T00:00:00")
    with TestClient(create_app(late)) as client:
        login(client, development_player_seeds()[0].code)
        page = client.get("/admin/season")
        response = client.post(
            "/admin/season",
            data={
                "csrf_token": csrf(page.text),
                "game_opens_at": "2026-08-03T00:00",
                "prediction_locks_at": "2026-08-23T00:00",
            },
        )
        assert response.status_code == 422


def test_admin_cannot_view_or_export_predictions_before_reveal(database_url: str) -> None:
    settings = Settings(database_url=database_url, dev_now="2026-08-20T23:59:59")
    with TestClient(create_app(settings)) as client:
        login(client, development_player_seeds()[0].code)
        assert client.get("/admin/game/player/2").status_code == 403
        assert client.get("/admin/export/predictions/json").status_code == 422


def test_reinstatement_corrections_audit_exports_and_health(database_url: str) -> None:
    submit_prediction(database_url, 1)
    settings = Settings(database_url=database_url, dev_now="2026-09-01T12:00:00")
    app = create_app(settings)
    with TestClient(app) as client:
        login(client, development_player_seeds()[0].code)
        game = client.get("/admin/game")
        assert "Excluded" in game.text
        with app.state.session_factory() as session:
            season = get_current_season(session)
            assert season is not None
            team_ids = [item.team_id for item in get_season_teams(session, season.id)]
        player_form = client.get("/admin/game/player/3")
        reinstated = client.post(
            "/admin/game/player/3/reinstate",
            data={
                "csrf_token": csrf(player_form.text),
                "team_id": team_ids,
                "reason": "Verified entry omission",
            },
            follow_redirects=False,
        )
        assert reinstated.status_code == 303
        correction_form = client.get("/admin/game/player/2")
        corrected = client.post(
            "/admin/game/player/2/correct",
            data={
                "csrf_token": csrf(correction_form.text),
                "team_id": list(reversed(team_ids)),
                "reason": "Corrected entered order",
            },
            follow_redirects=False,
        )
        assert corrected.status_code == 303
        standings_form = client.get("/admin/game/standings")
        standings = client.post(
            "/admin/game/standings",
            data={
                "csrf_token": csrf(standings_form.text),
                "team_id": list(reversed(team_ids)),
                "reason": "Official table correction",
                "is_final": "yes",
            },
            follow_redirects=False,
        )
        assert standings.status_code == 303
        audit = client.get("/admin/audit")
        assert "player reinstated" in audit.text
        assert "prediction corrected" in audit.text
        assert "standings corrected" in audit.text
        predictions = client.get("/admin/export/predictions/csv")
        assert predictions.status_code == 200
        assert predictions.headers["content-type"].startswith("text/csv")
        assert "Configured Player" not in predictions.text
        standings_export = client.get("/admin/export/standings/json")
        assert standings_export.status_code == 200
        assert len(standings_export.json()) == 20
        scores = client.get("/admin/export/scores/json")
        assert scores.status_code == 200
        assert len(scores.json()) == 2
        health = client.get("/admin/health")
        assert "Database" in health.text and "Connected" in health.text
