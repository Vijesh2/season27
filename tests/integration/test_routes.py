import re

from httpx import Response
from starlette.testclient import TestClient

from app.auth.service import GENERIC_LOGIN_ERROR, development_player_seeds
from app.clock import clock_from_iso
from app.config import Settings
from app.main import create_app

ADMIN = development_player_seeds()[0]
REGULAR = development_player_seeds()[1]


def login(client: TestClient, code: str) -> Response:
    response = client.get("/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)', response.text)
    assert csrf is not None
    return client.post(
        "/login", data={"code": code, "csrf_token": csrf.group(1)}, follow_redirects=False
    )


def test_dashboard_requires_authentication(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_dashboard_shows_seeded_season_after_login(client: TestClient) -> None:
    assert login(client, f" {ADMIN.code.lower()} ").status_code == 303
    response = client.get("/")
    assert response.status_code == 200
    assert "Premier League 2026/27" in response.text
    assert "Prediction period" in response.text
    assert "Swap 4" in response.text
    assert f"Signed in as {ADMIN.display_name}" in response.text
    assert ">Admin<" in response.text
    assert "Season27" in response.text
    assert "Coventry City" in response.text
    assert "/static/brand/favicon-32.png" in response.text


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboard_uses_injected_clock_and_marks_override(database_url: str) -> None:
    settings = Settings(database_url=database_url, dev_now="2026-08-21T00:00:00")
    app = create_app(settings, clock_from_iso(settings.dev_now))
    with TestClient(app) as overridden_client:
        login(overridden_client, ADMIN.code)
        response = overridden_client.get("/")
    assert "Swap 1 open" in response.text
    assert "Development time override active" in response.text


def test_login_failure_is_generic_and_csrf_is_required(client: TestClient) -> None:
    failed = login(client, "NOPE")
    assert failed.status_code == 200
    assert GENERIC_LOGIN_ERROR in failed.text
    expired = client.post("/login", data={"code": ADMIN.code})
    assert expired.status_code == 403
    assert 'href="/login"' in expired.text


def test_session_cookie_security_attributes(database_url: str) -> None:
    settings = Settings(database_url=database_url, environment="production")
    with TestClient(create_app(settings), base_url="https://testserver") as secure_client:
        response = login(secure_client, ADMIN.code)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Secure" in cookie
    assert f"Max-Age={300 * 24 * 60 * 60}" in cookie


def test_admin_authorization(client: TestClient) -> None:
    login(client, REGULAR.code)
    assert client.get("/admin").status_code == 403
    client.cookies.clear()
    login(client, ADMIN.code)
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Season27 administration" in response.text
    assert 'href="/"' in response.text


def test_logout_revokes_current_session(client: TestClient) -> None:
    login(client, ADMIN.code)
    dashboard = client.get("/")
    csrf = re.search(r'name="csrf_token" value="([^"]+)', dashboard.text)
    assert csrf is not None
    response = client.post("/logout", data={"csrf_token": csrf.group(1)}, follow_redirects=False)
    assert response.status_code == 303
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"


def test_logout_rejects_bad_csrf(client: TestClient) -> None:
    login(client, ADMIN.code)
    assert client.post("/logout", data={"csrf_token": "bad"}).status_code == 403
