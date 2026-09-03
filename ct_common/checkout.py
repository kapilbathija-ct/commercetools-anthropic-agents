"""commercetools Checkout: the Sessions API and the cart preparation it requires.

Checkout in ``PaymentOnly`` mode owns the payment step and creates the Payment and the
Order itself; this project's job is to hand it a cart that is ready and then mount the
widget. Nothing here charges a card or places an order.

Four constraints decide the sequence, each one learned the hard way on this project:

* The Sessions API lives on its own host (``session.{region}.commercetools.com``) and needs
  a token with ``manage_sessions``, which is a **separate grant** from ``manage_project``.
  An API client's scopes cannot be edited after creation, so a client without it has to be
  replaced rather than amended.
* ``POST /orders`` fails with ``Shipping address is not set.`` for a cart with shippable
  line items, so the address goes on the cart before a session is created.
* Checkout rejects a Frozen cart outright (``CartInvalidStateError``), and it will not even
  read an already-computed price off one. A cart must be Active when the session is created.
* The connector's processor is a scale-to-zero service. Checkout's own backend calls it, so
  the storefront can no longer see that call -- warming it is still worth doing.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from .config import CTSettings
from .errors import CTError

logger = logging.getLogger(__name__)


class CheckoutSessions:
    def __init__(
        self,
        settings: CTSettings,
        *,
        application_key: str,
        processor_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._application_key = application_key
        self._processor_url = processor_url
        self._http = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0))

    @property
    def region(self) -> str:
        """``api.us-central1.gcp.commercetools.com`` -> ``us-central1.gcp``."""
        host = self._settings.api_url.replace("https://", "").replace("http://", "")
        return host.replace("api.", "", 1).replace(".commercetools.com", "").strip("/")

    @property
    def session_host(self) -> str:
        return f"https://session.{self.region}.commercetools.com"

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _sessions_token(self) -> str:
        """A token scoped to ``manage_sessions`` only. Not cached alongside the core token:
        it is a different grant and a checkout is rare next to a catalogue read."""
        credentials = f"{self._settings.client_id}:{self._settings.client_secret}"
        response = await self._http.post(
            self._settings.token_url,
            data={
                "grant_type": "client_credentials",
                "scope": f"manage_sessions:{self._settings.project_key}",
            },
            headers={"Authorization": "Basic " + base64.b64encode(credentials.encode()).decode()},
        )
        if response.status_code != 200:
            raise CTError(
                "could not obtain a manage_sessions token; the API client needs that scope "
                "granted at creation, and an existing client's scopes cannot be edited",
                status=response.status_code,
            )
        return response.json()["access_token"]

    async def warm_processor(self) -> None:
        """Fire and forget. The connector's processor scales to zero and Checkout's backend
        calls it a few seconds after the session is created; a cold one shows up as a
        generic "Payment failed" that looks like nothing in particular."""
        if not self._processor_url:
            return
        try:
            await self._http.get(f"{self._processor_url}/operations/status", timeout=5.0)
        except httpx.HTTPError as error:
            logger.debug("processor warm-up failed harmlessly: %s", error)

    async def create(self, cart_id: str, future_order_number: str | None = None) -> dict[str, Any]:
        """A Checkout Session for one cart. Created as late as possible -- when the shopper
        actually reaches the payment step -- because sessions expire."""
        token = await self._sessions_token()
        metadata: dict[str, Any] = {"applicationKey": self._application_key}
        if future_order_number:
            metadata["futureOrderNumber"] = future_order_number
        response = await self._http.post(
            f"{self.session_host}/{self._settings.project_key}/sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={"cart": {"cartRef": {"id": cart_id}}, "metadata": metadata},
        )
        if response.status_code >= 300:
            raise CTError(
                f"could not create a checkout session: {response.text[:300]}",
                status=response.status_code,
            )
        session = response.json()
        return {
            "sessionId": session["id"],
            "projectKey": self._settings.project_key,
            "region": self.region,
        }
