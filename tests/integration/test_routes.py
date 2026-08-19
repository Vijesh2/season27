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


def test_media_predictions_are_public(client: TestClient) -> None:
    response = client.get("/media-predictions")
    assert response.status_code == 200
    assert "The Athletic" in response.text
    assert "17 August 2026" in response.text
    assert response.text.index("Arsenal") < response.text.index("Manchester City")
    assert response.text.index("Coventry City") < response.text.index("Hull City")
    assert "do not participate in the game" in response.text
    assert 'href="/login"' in response.text
    assert "Opta Analyst" in response.text
    assert "Updated 19 August 2026" in response.text
    assert "Third-party predictions" in response.text
    assert "Projected points" not in response.text
    assert "73.35" not in response.text
    assert response.text.count("prediction-comparison-table") == 1
    assert 'aria-label="Third-party prediction comparison table"' in response.text
    assert "Select a publisher name to view its original prediction." in response.text
    assert response.text.index("Liverpool") < response.text.index("Aston Villa")


def test_how_to_play_accordion_is_available_before_and_after_login(
    client: TestClient,
) -> None:
    login_page = client.get("/login")
    assert "DEVELOPMENT — LOCAL ONLY" in login_page.text
    assert 'href="/media-predictions"' in login_page.text
    assert "Compare predictions from independent publishers and platforms" in login_page.text
    assert "View third-party predictions; no sign-in required" in login_page.text
    assert "<details" in login_page.text
    assert "<summary>How to play</summary>" in login_page.text
    assert "Predict the table" in login_page.text

    assert login(client, ADMIN.code).status_code == 303
    for path in ("/", "/prediction"):
        page = client.get(path)
        assert page.status_code == 200
        assert page.text.count("<summary>How to play</summary>") == 1


def test_dashboard_shows_seeded_season_after_login(client: TestClient) -> None:
    assert login(client, f" {ADMIN.code.lower()} ").status_code == 303
    response = client.get("/")
    assert response.status_code == 200
    assert "Premier League 2026/27" in response.text
    assert "Prediction period" in response.text
    assert "How to play" in response.text
    assert "Predict the table" in response.text
    assert "prediction lock at 00:00 BST on 21 August 2026" in response.text
    assert "revise and resubmit" in response.text
    assert "Use your swaps" in response.text
    assert "21 August–31 October 2026" in response.text
    assert "cannot be undone" in response.text
    assert "does not carry over" in response.text
    assert "Lowest score wins" in response.text
    assert 'href="/prediction"' in response.text
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
    settings = Settings(
        database_url=database_url,
        environment="production",
        secret_key="a-production-strength-test-secret",
        bootstrap_admin_name=ADMIN.display_name,
        bootstrap_admin_code=ADMIN.code,
    )
    with TestClient(create_app(settings), base_url="https://testserver") as secure_client:
        assert "environment-banner" not in secure_client.get("/login").text
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
