"""One authenticated commercetools client per process.

Two transports, chosen per call for a measured reason:

REST for catalog reads, where ``filter[attributes]``, price selection and locale
projection all work and keep a response small.

GraphQL for cart and order reads, where they do not. ``filter[attributes]`` is silently
ignored on ``/carts`` and carts have no locale projection at all, so a REST cart on a wide
Product Type carries every attribute of every line item: 19 KB on a 22-attribute type and
103 KB on a 99-attribute one, measured live 2026-08-17. The same cart read through GraphQL
with an explicit field selection is 2,606 B, byte-identical across both types. The agent's
fenced-result cap is 12,000 characters, so this is the difference between a cart the model
can read and one that is silently cut short.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .config import CTSettings
from .errors import CartFrozen, CTError, TaxCategoryMissing

logger = logging.getLogger(__name__)

# Refresh this far before expiry rather than trusting a long-lived token: the prior build
# deliberately avoided depending on a token baked in at deploy time going stale mid-run.
_TOKEN_MARGIN_S = 60.0


class CTClient:
    def __init__(self, settings: CTSettings, *, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._http = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0))
        self._token: str | None = None
        self._expires_at = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def settings(self) -> CTSettings:
        return self._settings

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- Auth ---------------------------------------------------------------------------

    async def _access_token(self) -> str:
        if self._token and time.monotonic() < self._expires_at - _TOKEN_MARGIN_S:
            return self._token
        async with self._token_lock:
            if self._token and time.monotonic() < self._expires_at - _TOKEN_MARGIN_S:
                return self._token
            response = await self._http.post(
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

    async def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._access_token()}"}

    # -- REST ---------------------------------------------------------------------------

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def get_list(self, path: str, params: dict[str, Any] | None = None) -> list[Any]:
        """Endpoints that answer with a bare JSON array rather than a paged object.
        ``/shipping-methods`` and ``/shipping-methods/matching-cart`` are both like this,
        so reading ``results`` off them raises an AttributeError at runtime."""
        payload = await self._request("GET", path, params=params)
        if isinstance(payload, list):
            return payload
        return payload.get("results") or []

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, json=body)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._settings.base_url}{path}"
        response = await self._http.request(method, url, headers=await self._headers(), **kwargs)
        if response.status_code == 404:
            return {}
        if response.status_code >= 400:
            raise self._error(response)
        return response.json()

    def _error(self, response: httpx.Response) -> CTError:
        """Map the platform's own error code before anything else sees a bare 400: the two
        cases below are ordinary states of a live storefront, not faults."""
        code = message = ""
        try:
            payload = response.json()
            first = (payload.get("errors") or [{}])[0]
            code = first.get("code", "")
            message = first.get("message", "") or payload.get("message", "")
        except ValueError:
            message = response.text[:200]
        lowered = message.lower()
        if "frozen" in lowered:
            return CartFrozen(message, status=response.status_code, code=code)
        if "tax category" in lowered or code == "MissingTaxRateForCountry":
            return TaxCategoryMissing(message, status=response.status_code, code=code)
        logger.warning("commercetools %s %s: %s", response.status_code, code, message[:200])
        return CTError(
            message or "commercetools call failed",
            status=response.status_code,
            code=code,
        )

    # -- GraphQL ------------------------------------------------------------------------

    async def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._http.post(
            self._settings.graphql_url,
            headers=await self._headers(),
            json={"query": query, "variables": variables or {}},
        )
        if response.status_code >= 400:
            raise self._error(response)
        payload = response.json()
        if payload.get("errors"):
            first = payload["errors"][0]
            message = first.get("message", "GraphQL error")
            lowered = message.lower()
            if "frozen" in lowered:
                raise CartFrozen(message, status=200, code=first.get("code"))
            raise CTError(message, status=200, code=first.get("code"))
        return payload.get("data") or {}

    # -- Price selection ----------------------------------------------------------------

    def price_params(self) -> dict[str, str]:
        """Every catalog read carries these. Without them a Product Search result is
        ``{"id": "..."}`` and nothing else -- no name, no image, no price."""
        return {
            "priceCurrency": self._settings.currency,
            "priceCountry": self._settings.country,
            "localeProjection": self._settings.locale,
        }

    async def delete(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("DELETE", path, params=params)
