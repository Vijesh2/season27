import re

from starlette.testclient import TestClient

from app.auth.service import development_player_seeds
from app.config import Settings
from app.main import create_app


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


def submit_prediction(client: TestClient, code: str) -> None:
    login(client, code)
    review = client.get("/prediction/review")
    csrf = re.search(r'name="csrf_token" value="([^"]+)', review.text)
    assert csrf
    response = client.post(
        "/prediction/submit",
        data={"csrf_token": csrf.group(1), "confirmed": "yes"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def prepare_submitted_players(database_url: str, count: int = 1) -> None:
    settings = Settings(database_url=database_url, dev_now="2026-08-20T12:00:00")
    seeds = development_player_seeds()
    for index in range(1, count + 1):
        with TestClient(create_app(settings)) as client:
            submit_prediction(client, seeds[index].code)


def swap_client(database_url: str, player_index: int = 1) -> TestClient:
    settings = Settings(database_url=database_url, dev_now="2026-09-01T12:00:00")
    client = TestClient(create_app(settings))
    login(client, development_player_seeds()[player_index].code)
    return client


def swap_form(page: str) -> tuple[str, list[str]]:
    csrf = re.search(r'name="csrf_token" value="([^"]+)', page)
    ids = re.findall(r'type="checkbox" name="team_id" value="([0-9]+)', page)
    assert csrf and len(ids) == 20
    return csrf.group(1), ids


def test_swap_page_is_private_and_requires_locked_prediction(database_url: str) -> None:
    with TestClient(create_app(Settings(database_url=database_url))) as anonymous:
        assert anonymous.get("/swaps", follow_redirects=False).status_code == 303
    prepare_submitted_players(database_url)
    with swap_client(database_url) as client:
        page = client.get("/swaps")
        assert page.status_code == 200
        assert "Swap 1 open" in client.get("/").text
        assert ">Open<" in page.text
        assert page.text.count('name="team_id"') == 20


def test_player_previews_confirms_and_cannot_reuse_window(database_url: str) -> None:
    prepare_submitted_players(database_url)
    with swap_client(database_url) as client:
        initial = client.get("/swaps")
        csrf, ids = swap_form(initial.text)
        assert client.post("/swaps/preview", data={"csrf_token": "bad"}).status_code == 403
        invalid = client.post(
            "/swaps/preview", data={"csrf_token": csrf, "team_id": [ids[0]]}
        )
        assert "Select exactly two teams" in invalid.text
        preview = client.post(
            "/swaps/preview",
            data={"csrf_token": csrf, "team_id": [ids[0], ids[-1]]},
        )
        assert preview.status_code == 200
        assert "Preview" in preview.text
        assert "position 1 to 20" in preview.text
        assert "position 20 to 1" in preview.text
        unchanged_prediction = client.get("/prediction").text
        assert unchanged_prediction.find(ids[0]) < unchanged_prediction.find(ids[-1])
        assert (
            client.post(
                "/swaps/confirm",
                data={
                    "csrf_token": csrf,
                    "first_team_id": ids[0],
                    "second_team_id": ids[-1],
                },
            ).status_code
            == 422
        )
        applied = client.post(
            "/swaps/confirm",
            data={
                "csrf_token": csrf,
                "first_team_id": ids[0],
                "second_team_id": ids[-1],
                "confirmed": "yes",
            },
            follow_redirects=False,
        )
        assert applied.status_code == 303
        receipt = client.get(applied.headers["location"])
        assert "Swap applied successfully" in receipt.text
        assert ">Used<" in receipt.text
        assert "1 → 20" in receipt.text
        assert "Make this window's swap" not in receipt.text
        repeated = client.post(
            "/swaps/confirm",
            data={
                "csrf_token": csrf,
                "first_team_id": ids[1],
                "second_team_id": ids[2],
                "confirmed": "yes",
            },
        )
        assert repeated.status_code == 422
        assert "already been used" in repeated.text


def test_player_cannot_view_another_players_swap_history(database_url: str) -> None:
    prepare_submitted_players(database_url, count=2)
    with swap_client(database_url, 1) as first:
        csrf, ids = swap_form(first.get("/swaps").text)
        first.post(
            "/swaps/confirm",
            data={
                "csrf_token": csrf,
                "first_team_id": ids[0],
                "second_team_id": ids[-1],
                "confirmed": "yes",
            },
        )
    with swap_client(database_url, 2) as second:
        page = second.get("/swaps?player_id=1")
        assert "No swaps used yet" in page.text
        assert ">Used<" not in page.text
