"""Deployment settings read from the environment once per process.

The variable names match ``commercetools-es-workspace``'s ``.env`` so one credential set
serves both. ``currency``, ``country`` and ``locale`` are not optional conveniences: a
Product Search call without price selection and locale projection returns records with no
name, image or price at all (verified live 2026-08-05 against this project), so every read
in this package passes them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class CTSettings:
    project_key: str
    client_id: str
    client_secret: str
    auth_url: str
    api_url: str
    currency: str = "USD"
    country: str = "US"
    locale: str = "en-US"
    # Carts are created in this Store when set. There is no update action that sets
    # ``store`` on an existing Cart, so a Store-scoped shipping predicate or price that is
    # not decided here can never be applied later -- the cart has to be recreated.
    store_key: str | None = None
    # Session transcripts. Unset falls back to the in-process store, which splits sessions
    # across workers; the prior build shipped that and flagged it as its first fragility.
    redis_url: str | None = None
    # Read-only warehouse view behind the merchant analysis delegate (order events reach it
    # through Subscriptions -> Pub/Sub -> BigQuery). Unset leaves the delegate off.
    analysis_bq_dataset: str | None = None

    @property
    def base_url(self) -> str:
        return f"{self.api_url.rstrip('/')}/{self.project_key}"

    @property
    def graphql_url(self) -> str:
        return f"{self.base_url}/graphql"

    @property
    def token_url(self) -> str:
        return f"{self.auth_url.rstrip('/')}/oauth/token"


def load_settings(env_file: str | os.PathLike[str] | None = ".env") -> CTSettings:
    """Environment first, then the ``.env`` file, so a deployment's real secret manager
    wins over anything on disk."""
    if env_file:
        load_dotenv(env_file, override=False)
    required = (
        "CTP_PROJECT_KEY",
        "CTP_CLIENT_ID",
        "CTP_CLIENT_SECRET",
        "CTP_AUTH_URL",
        "CTP_API_URL",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"commercetools credentials missing from the environment: {', '.join(missing)}"
        )
    return CTSettings(
        project_key=os.environ["CTP_PROJECT_KEY"],
        client_id=os.environ["CTP_CLIENT_ID"],
        client_secret=os.environ["CTP_CLIENT_SECRET"],
        auth_url=os.environ["CTP_AUTH_URL"],
        api_url=os.environ["CTP_API_URL"],
        currency=os.environ.get("CT_CURRENCY", "USD"),
        country=os.environ.get("CT_COUNTRY", "US"),
        locale=os.environ.get("CT_LOCALE", "en-US"),
        store_key=os.environ.get("CTP_STORE_KEY") or None,
        redis_url=os.environ.get("REDIS_URL") or None,
        analysis_bq_dataset=os.environ.get("CT_ANALYSIS_BQ_DATASET") or None,
    )
