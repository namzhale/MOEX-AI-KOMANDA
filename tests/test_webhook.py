from arena_bot.webhook import WebhookNotifier


class RecordingSession:
    def __init__(self):
        self.posts = []

    def post(self, url, json, timeout):
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        return Response()


class Response:
    def raise_for_status(self):
        return None


class FailingSession:
    def post(self, *args, **kwargs):
        raise RuntimeError("webhook down")


def test_webhook_notifier_sends_event_payload_without_secrets():
    session = RecordingSession()
    notifier = WebhookNotifier(
        url="https://webhook.site/example",
        source="Team24ArenaBot",
        session=session,
    )

    ok = notifier.send("cycle_finished", {"status": "submitted", "token": "secret"})

    assert ok is True
    assert session.posts == [
        {
            "url": "https://webhook.site/example",
            "json": {
                "source": "Team24ArenaBot",
                "event": "cycle_finished",
                "payload": {"status": "submitted"},
            },
            "timeout": 5,
        }
    ]


def test_webhook_notifier_is_noop_without_url():
    session = RecordingSession()
    notifier = WebhookNotifier(url=None, source="Team24ArenaBot", session=session)

    ok = notifier.send("startup", {"status": "ok"})

    assert ok is False
    assert session.posts == []


def test_webhook_notifier_swallows_delivery_errors():
    notifier = WebhookNotifier(
        url="https://webhook.site/example",
        source="Team24ArenaBot",
        session=FailingSession(),
    )

    ok = notifier.send("cycle_failed", {"error": "boom"})

    assert ok is False
