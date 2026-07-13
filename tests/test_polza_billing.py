from __future__ import annotations

import httpx

from agent.llm.billing import PolzaBillingClient


def test_polza_billing_reads_amount_from_balance_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"amount": "1250.50"})

    client = PolzaBillingClient(
        api_key="secret-token",
        base_url="https://polza.ai/api/v1",
        transport=httpx.MockTransport(handler),
    )

    assert client.get_balance_amount() == 1250.50
    assert requests[0].url.path == "/api/v1/balance"
    assert requests[0].headers["Authorization"] == "Bearer secret-token"
