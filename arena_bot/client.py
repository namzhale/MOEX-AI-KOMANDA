from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class ArenagoClient:
    api_base_url: str
    api_token: str
    timeout_seconds: int = 10

    def submit_order(self, direction: str, secid: str, quantity: int, bot: str) -> dict:
        response = requests.post(
            f"{self.api_base_url.rstrip('/')}/api/submit_order",
            json={
                "direction": direction,
                "secid": secid,
                "quantity": quantity,
                "bot": bot,
            },
            headers={
                "Content-Type": "application/json",
                "Authorization": self.api_token,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError(payload["error"])
        return payload
