from app.config import Settings
from scripts import seed_development


def test_seed_command(monkeypatch: object, database_url: str, capsys: object) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        seed_development, "Settings", lambda: Settings(database_url=database_url)
    )
    seed_development.main()
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "Season 2026/27, 5 development players, and 20 teams are ready." in output
