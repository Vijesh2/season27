import json
import logging

from app.runtime import JsonFormatter


def test_json_log_formatter_produces_machine_readable_event() -> None:
    record = logging.LogRecord("season27", logging.INFO, __file__, 1, "ready", (), None)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["level"] == "INFO"
    assert payload["message"] == "ready"
