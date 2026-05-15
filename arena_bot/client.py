from dataclasses import dataclass

import requests


class ArenagoApiError(RuntimeError):
    pass


@dataclass
class ArenagoClient:
    api_base_url: str
    api_token: str
    timeout_seconds: int = 10
    session: requests.Session | None = None

    def submit_order(self, direction: str, secid: str, quantity: int, bot: str) -> dict:
        url = f"{self.api_base_url.rstrip('/')}/api/submit_order"
        http = self.session or requests
        try:
            response = http.post(
                url,
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
        except requests.RequestException as exc:
            raise ArenagoApiError(f"POST {url} failed: {type(exc).__name__}: {exc}") from exc

        payload = response.json()
        if isinstance(payload, dict) and payload.get("error"):
            raise ArenagoApiError(payload["error"])
        return payload
