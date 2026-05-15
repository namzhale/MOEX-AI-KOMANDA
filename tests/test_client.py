import pytest
import requests

from arena_bot.client import ArenagoApiError, ArenagoClient


class FailingSession:
    def post(self, *args, **kwargs):
        raise requests.ConnectionError("network is unreachable")


class ErrorResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"error": "ERROR: MARKET CLOSED"}


class ErrorSession:
    def post(self, *args, **kwargs):
        return ErrorResponse()


def test_client_wraps_network_errors_without_token():
    client = ArenagoClient("https://arenago.ru", "secret-token", session=FailingSession())

    with pytest.raises(ArenagoApiError) as exc:
        client.submit_order("B", "SBER", 1, "Team24ArenaBot")

    message = str(exc.value)
    assert "POST https://arenago.ru/api/submit_order failed" in message
    assert "ConnectionError" in message
    assert "secret-token" not in message


def test_client_reports_api_error_payload():
    client = ArenagoClient("https://arenago.ru", "secret-token", session=ErrorSession())

    with pytest.raises(ArenagoApiError) as exc:
        client.submit_order("B", "SBER", 1, "Team24ArenaBot")

    assert str(exc.value) == "ERROR: MARKET CLOSED"
