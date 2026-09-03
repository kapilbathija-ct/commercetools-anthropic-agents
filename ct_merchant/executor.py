"""The merchant executor subclass.

``ChangeNotApplicable`` is already mapped by the role's own executor. What this adds is the
commercetools states an operator can act on, so a refusal reads as a fact about the store
rather than as an outage.
"""

from __future__ import annotations

from commerce_common.execution import ToolOutcome
from merchant_agent.executor import MerchantToolExecutor

from ct_common import CTError

CONCURRENT_TEXT = (
    "Another edit reached that record first, so nothing was written. Re-read the listing "
    "and stage the change again against its current values."
)


class CTMerchantToolExecutor(MerchantToolExecutor):
    def domain_error(self, error: Exception) -> ToolOutcome | None:
        # commercetools rejects a write whose version is behind the stored one. That is a
        # concurrency fact, not a failure, and the operator's next step is different.
        if isinstance(error, CTError) and error.code in {
            "ConcurrentModification",
            "InvalidCurrentPassword",
        }:
            return ToolOutcome.error(CONCURRENT_TEXT)
        return super().domain_error(error)
