"""The staged-change ledger over commercetools Custom Objects.

The reference's ``ChangeLedger`` keeps changes in a process dict, which is right for one
long-lived worker and wrong the moment the process is not the same one next request. On
serverless there is no instance affinity at all, so a change staged in one invocation is
gone by the time the operator clicks Approve in the next -- the whole propose/approve cycle
spans two requests by design.

This keeps the same lifecycle and the same guardrail checks, with the changes in a Custom
Object container instead. Two details differ from the in-memory version on purpose:

* **Ids are random, not sequential.** ``chg-0001`` comes from a per-process counter, which
  two instances would both hand out.
* **Every read goes to the platform**, so an operator sees the queue as it actually is
  rather than as their instance last saw it.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from merchant_agent import ActorKind, ChangeItem, ChangeKind, ChangeStatus, StagedChange
from merchant_agent.changes import (
    ChangeNotApplicable,
    GuardrailViolation,
    check_guardrails,
)

from ct_common.sync_client import CTSyncClient

CONTAINER = "agent-staged-changes"


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class CustomObjectChangeLedger:
    """The same surface the merchant backend uses from ``ChangeLedger``."""

    def __init__(self, config: Any, client: CTSyncClient) -> None:
        self._config = config
        self._client = client

    # -- Storage -------------------------------------------------------------------------

    def _write(self, change: StagedChange) -> None:
        self._client.post(
            "/custom-objects",
            {
                "container": CONTAINER,
                "key": change.change_id,
                "value": change.model_dump(mode="json"),
            },
        )

    def _read(self, change_id: str) -> StagedChange | None:
        stored = self._client.get(f"/custom-objects/{CONTAINER}/{change_id}")
        if not stored:
            return None
        return StagedChange.model_validate(stored["value"])

    def _all(self) -> list[StagedChange]:
        page = self._client.get(f"/custom-objects/{CONTAINER}", {"limit": 200})
        return [StagedChange.model_validate(o["value"]) for o in page.get("results") or []]

    # -- Lifecycle -----------------------------------------------------------------------

    def stage(
        self,
        *,
        kind: ChangeKind,
        summary: str,
        items: list[ChangeItem],
        actor: str,
        actor_kind: ActorKind = ActorKind.OPERATOR,
        currency: str | None = None,
        margin_impact: float | None = None,
        margin_before_pct: float | None = None,
        margin_after_pct: float | None = None,
        guardrail_notes: list[str] | None = None,
    ) -> StagedChange:
        violations = check_guardrails(kind, items, self._config)
        if violations:
            raise GuardrailViolation(violations)
        change = StagedChange(
            # Random rather than sequential: a per-process counter would collide across
            # instances, and a change id is what approval is granted against.
            change_id=f"chg-{secrets.token_hex(6)}",
            kind=kind,
            status=ChangeStatus.STAGED,
            summary=_truncate(summary, 200),
            items=items,
            created_at=datetime.now(UTC),
            created_by=actor,
            created_by_kind=actor_kind,
            guardrail_notes=guardrail_notes or [],
            currency=currency,
            margin_impact=margin_impact,
            margin_before_pct=margin_before_pct,
            margin_after_pct=margin_after_pct,
        )
        self._write(change)
        return change

    def get(self, change_id: str) -> StagedChange | None:
        return self._read(change_id)

    def pending(self) -> list[StagedChange]:
        return [c for c in self._all() if c.status is ChangeStatus.STAGED]

    def applied(self) -> list[StagedChange]:
        return [c for c in self._all() if c.status is ChangeStatus.APPLIED]

    def resolved(self) -> list[StagedChange]:
        return [c for c in self._all() if c.status is not ChangeStatus.STAGED]

    def apply(self, change_id: str, actor: str) -> StagedChange:
        change = self._require_staged(change_id, "apply")
        # The config may have tightened since the change was staged.
        violations = check_guardrails(change.kind, change.items, self._config)
        if violations:
            raise GuardrailViolation(violations)
        updated = change.model_copy(
            update={
                "status": ChangeStatus.APPLIED,
                "applied_at": datetime.now(UTC),
                "applied_by": actor,
            }
        )
        self._write(updated)
        return updated

    def discard(
        self, change_id: str, actor: str, actor_kind: ActorKind = ActorKind.OPERATOR
    ) -> StagedChange:
        change = self._require_staged(change_id, "discard")
        updated = change.model_copy(
            update={
                "status": ChangeStatus.DISCARDED,
                "discarded_at": datetime.now(UTC),
                "discarded_by": actor,
                "discarded_by_kind": actor_kind,
            }
        )
        self._write(updated)
        return updated

    def _require_staged(self, change_id: str, action: str) -> StagedChange:
        change = self._read(change_id)
        if change is None:
            raise ChangeNotApplicable(f"no change with id {change_id!r} to {action}")
        if change.status is not ChangeStatus.STAGED:
            raise ChangeNotApplicable(
                f"change {change_id} is {change.status.value}, not staged — nothing to {action}"
            )
        return change
