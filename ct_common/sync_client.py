"""A synchronous commercetools client, for the session store and the change ledger.

Both of those implement interfaces the reference defines as **synchronous**, and FastAPI
runs a sync dependency and a sync background task in its threadpool -- so blocking there
blocks a worker thread, not the event loop.

Reusing the async :class:`~ct_common.client.CTClient` from a sync caller does not work: its
``httpx.AsyncClient`` binds its connection pool to the first event loop it runs on, so
driving it from a fresh loop per call fails with ``Event loop is closed`` as soon as a
pooled connection is reused. A separate sync client with its own pool avoids the problem
rather than working around it.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from .config import CTSettings
from .errors import CTError

_TOKEN_MARGIN_S = 60.0


class CTSyncClient:
    def __init__(self, settings: CTSettings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._http = client or httpx.Client(timeout=httpx.Timeout(20.0))
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    @property
    def settings(self) -> CTSettings:
        return self._settings

    def close(self) -> None:
        self._http.close()

    def _access_token(self) -> str:
        if self._token and time.monotonic() < self._expires_at - _TOKEN_MARGIN_S:
            return self._token
        with self._lock:
            if self._token and time.monotonic() < self._expires_at - _TOKEN_MARGIN_S:
                return self._token
            response = self._http.post(
                self._settings.token_url,
                data={"grant_type": "client_credentials"},
                auth=(self._settings.client_id, self._settings.client_secret),
            )
            if response.status_code != 200:
                raise CTError("commercetools token request failed", status=response.status_code)
            payload = response.json()
            self._token = payload["access_token"]
            self._expires_at = time.monotonic() + float(payload.get("expires_in", 3600))
            return self._token

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(
            method,
            f"{self._settings.base_url}{path}",
            headers={"Authorization": f"Bearer {self._access_token()}"},
            **kwargs,
        )
        if response.status_code == 404:
            return {}
        if response.status_code >= 400:
            code = message = ""
            try:
                first = (response.json().get("errors") or [{}])[0]
                code, message = first.get("code", ""), first.get("message", "")
            except ValueError:
                message = response.text[:200]
            raise CTError(
                message or "commercetools call failed", status=response.status_code, code=code
            )
        return response.json()

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("GET", path, params=params)

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", path, json=body)

    def delete(self, path: str) -> dict[str, Any]:
        return self.request("DELETE", path)
