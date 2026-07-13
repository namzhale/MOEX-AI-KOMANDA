from __future__ import annotations

from agent.webhook import WebhookNotifier, sanitize_payload


def test_sanitize_payload_strips_secrets() -> None:
    clean = sanitize_payload({"status": "ok", "token": "secret", "api_key": "x"})
    assert clean == {"status": "ok"}


def test_webhook_notifier_noop_without_url() -> None:
    notifier = WebhookNotifier(url=None, source="team-24")
    assert notifier.send("startup", {"status": "ok"}) is False


def test_webhook_notifier_sends_sanitized_body(mocker) -> None:
    captured: list[dict] = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

    def fake_post(url, json):
        captured.append({"url": url, "json": json})
        return FakeResponse()

    client = mocker.Mock()
    client.post = fake_post
    notifier = WebhookNotifier(url="https://example.com/hook", source="Team24", _client=client)

    ok = notifier.send("cycle_finished", {"status": "submitted", "token": "secret"})

    assert ok is True
    assert captured[0]["json"] == {
        "source": "Team24",
        "event": "cycle_finished",
        "payload": {"status": "submitted"},
    }
