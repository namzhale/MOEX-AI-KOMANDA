"""Тесты ArenaGo: DRY_RUN, submit_order, чтение портфеля."""

from __future__ import annotations

from agent.data.arenago import ArenaGoClient, _fingerprint


def _mock_resp(mocker, payload):
    r = mocker.Mock()
    r.raise_for_status = mocker.Mock()
    r.json = mocker.Mock(return_value=payload)
    return r


def test_submit_order_dry_run_does_not_call_network(mocker) -> None:
    client = ArenaGoClient(
        base_url="http://example.invalid", api_key="x", bot="t24", dry_run=True
    )
    spy_post = mocker.patch.object(client._client, "post")

    out = client.submit_order("SBER", "B", quantity=10)

    assert out["status"] == "DRY_RUN"
    assert out["order"] == {"direction": "B", "secid": "SBER", "quantity": 10, "bot": "t24"}
    spy_post.assert_not_called()


def test_submit_order_live_posts_correct_payload(mocker) -> None:
    client = ArenaGoClient(
        base_url="http://example.invalid", api_key="x", bot="t24", dry_run=False
    )
    spy_post = mocker.patch.object(
        client._client,
        "post",
        return_value=_mock_resp(
            mocker,
            {"success": True, "order_value": 3000, "price": 300.0, "remaining_cash": 99700},
        ),
    )

    out = client.submit_order("SBER", "B", quantity=10)
    spy_post.assert_called_once()
    args, kwargs = spy_post.call_args
    assert args[0] == "/api/submit_order"
    assert kwargs["json"] == {"direction": "B", "secid": "SBER", "quantity": 10, "bot": "t24"}
    assert out["success"] is True


def test_authorization_header_has_no_bearer_prefix() -> None:
    client = ArenaGoClient(base_url="http://example.invalid", api_key="raw-token", bot="t24")
    assert client._client.headers["Authorization"] == "raw-token"


def test_key_fingerprint_never_contains_full_token() -> None:
    fp = _fingerprint("raw-token")

    assert fp["len"] == 9
    assert fp["prefix"] == "raw-"
    assert fp["suffix"] == "oken"
    assert "value" not in fp


def test_get_portfolio_merges_bots_and_positions(mocker) -> None:
    client = ArenaGoClient(
        base_url="http://example.invalid", api_key="x", bot="t24", dry_run=True
    )
    mocker.patch.object(
        client._client,
        "get",
        side_effect=[
            _mock_resp(
                mocker,
                [
                    {"name": "other", "cash_balance": 1000},
                    {"name": "t24", "cash_balance": 250000.5},
                ],
            ),
            _mock_resp(
                mocker,
                [
                    {"secid": "SBER", "position": 10, "average_price": 300.0, "bot": "t24"},
                ],
            ),
        ],
    )

    portfolio = client.get_portfolio()
    assert portfolio["bot"] == "t24"
    assert portfolio["cash"] == 250000.5
    assert portfolio["positions"][0]["secid"] == "SBER"
