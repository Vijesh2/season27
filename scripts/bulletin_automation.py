import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

import requests
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AutomationError(RuntimeError):
    pass


class AutomationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SEASON27_", env_file=".env")

    public_base_url: str = ""
    bulletin_automation_token: SecretStr | None = None


def _configuration(args: argparse.Namespace) -> tuple[str, str]:
    settings = AutomationSettings()
    base_url = (args.base_url or settings.public_base_url).rstrip("/")
    token = args.token or (
        settings.bulletin_automation_token.get_secret_value()
        if settings.bulletin_automation_token
        else ""
    )
    if not base_url or not token:
        raise AutomationError(
            "Set SEASON27_PUBLIC_BASE_URL and SEASON27_BULLETIN_AUTOMATION_TOKEN."
        )
    return base_url, token


def _request(
    method: str,
    url: str,
    token: str,
    *,
    payload: dict[str, object] | None = None,
) -> dict[str, Any]:
    try:
        response = requests.request(
            method,
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=cast(Any, payload),
            timeout=(5, 60),
        )
        data = response.json()
    except (requests.RequestException, ValueError) as error:
        raise AutomationError("The Season27 automation endpoint could not be reached.") from error
    if not response.ok:
        detail = data.get("detail") if isinstance(data, dict) else None
        raise AutomationError(f"Season27 returned HTTP {response.status_code}: {detail or data}")
    if not isinstance(data, dict):
        raise AutomationError("Season27 returned an invalid response.")
    return data


def _period_payload(args: argparse.Namespace) -> dict[str, object]:
    payload: dict[str, object] = {}
    if args.period_start:
        payload["period_start"] = args.period_start
    if args.period_end:
        payload["period_end"] = args.period_end
    return payload


def _write(data: dict[str, Any], output: str | None) -> None:
    rendered = json.dumps(data, indent=2, sort_keys=True)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


def prepare(args: argparse.Namespace) -> None:
    base_url, token = _configuration(args)
    data = _request(
        "POST", f"{base_url}/internal/bulletins/prepare", token, payload=_period_payload(args)
    )
    _write(data, args.output)


def publish(args: argparse.Namespace) -> None:
    base_url, token = _configuration(args)
    prepared = json.loads(Path(args.fact_pack).read_text(encoding="utf-8"))
    fact_pack = prepared.get("fact_pack", {})
    payload = {
        "period_start": fact_pack.get("period_start"),
        "period_end": fact_pack.get("period_end"),
        "fact_pack_digest": prepared.get("fact_pack_digest"),
        "body": Path(args.body_file).read_text(encoding="utf-8"),
    }
    data = _request("POST", f"{base_url}/internal/bulletins/publish", token, payload=payload)
    _write(data, args.output)


def verify(args: argparse.Namespace) -> None:
    base_url, token = _configuration(args)
    data = _request("GET", f"{base_url}/internal/bulletins/{args.slug}", token)
    if data.get("status") != "published":
        raise AutomationError(f"Bulletin is not published: {data.get('status')}")
    _write(data, args.output)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run The Monday Morning Banter Bulletin")
    result.add_argument("--base-url")
    result.add_argument("--token", help=argparse.SUPPRESS)
    subparsers = result.add_subparsers(dest="command", required=True)
    for name, handler in (("prepare", prepare),):
        command = subparsers.add_parser(name, help="refresh sources and produce a factual pack")
        command.add_argument("--period-start")
        command.add_argument("--period-end")
        command.add_argument("--output")
        command.set_defaults(handler=handler)
    command = subparsers.add_parser("publish", help="validate and publish generated copy")
    command.add_argument("--fact-pack", required=True)
    command.add_argument("--body-file", required=True)
    command.add_argument("--output")
    command.set_defaults(handler=publish)
    command = subparsers.add_parser("verify", help="verify a published bulletin")
    command.add_argument("slug")
    command.add_argument("--output")
    command.set_defaults(handler=verify)
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        args.handler(args)
    except (AutomationError, OSError, json.JSONDecodeError) as error:
        print(f"bulletin automation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
